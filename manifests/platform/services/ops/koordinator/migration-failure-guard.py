"""Persist CustomPriority migration failures and exclude their workload's pods."""

import json
import os
import ssl
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

BLOCK = "koordinator.chik4ge.me/migration-blocked"
FAILURE = "koordinator.chik4ge.me/migration-failure"
RETRY_BASE_SECONDS = 120
RETRY_MAX_SECONDS = 1800
RETRY_RESET_SECONDS = 86400
RESOURCES = {
    "Deployment": ("apps/v1", "deployments"),
    "StatefulSet": ("apps/v1", "statefulsets"),
    "DaemonSet": ("apps/v1", "daemonsets"),
    "ReplicaSet": ("apps/v1", "replicasets"),
    "Job": ("batch/v1", "jobs"),
    "CronJob": ("batch/v1", "cronjobs"),
}


def owner(obj):
    return next((ref for ref in obj.get("metadata", {}).get("ownerReferences", [])
                 if ref.get("controller")), None)


def path(kind, namespace=None, name=None):
    group, resource = RESOURCES[kind]
    prefix = f"/apis/{group}"
    if namespace:
        prefix += f"/namespaces/{quote(namespace, safe='')}"
    result = f"{prefix}/{resource}"
    return result + f"/{quote(name, safe='')}" if name else result


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def failure_record(value):
    try:
        record = json.loads(value or "{}")
        return record if isinstance(record, dict) else {}
    except (TypeError, ValueError):
        return {}


def retry_deadline(record):
    deadline = parse_time(record.get("retryAfter"))
    if deadline is not None:
        return deadline
    observed = parse_time(record.get("observedAt"))
    return observed + timedelta(seconds=RETRY_BASE_SECONDS) if observed else None


class API:
    def __init__(self):
        self.base = "https://kubernetes.default.svc"
        self.credentials = "/var/run/secrets/kubernetes.io/serviceaccount"
        self.context = ssl.create_default_context(cafile=f"{self.credentials}/ca.crt")

    def __call__(self, endpoint, patch=None):
        with open(f"{self.credentials}/token") as token:
            headers = {"Authorization": f"Bearer {token.read().strip()}"}
        headers["Content-Type"] = "application/merge-patch+json"
        request = Request(self.base + endpoint, headers=headers,
                          data=json.dumps(patch).encode() if patch is not None else None,
                          method="PATCH" if patch is not None else "GET")
        try:
            with urlopen(request, context=self.context, timeout=10) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code == 404:
                return None
            raise


def workload(api, namespace, ref):
    """Follow controller ownership, checking UIDs to avoid a recreated namesake."""
    while ref and ref["kind"] in RESOURCES:
        endpoint = path(ref["kind"], namespace, ref["name"])
        obj = api(endpoint)
        if obj is None or obj["metadata"]["uid"] != ref["uid"]:
            return None
        parent = owner(obj)
        if not parent:
            return endpoint, obj
        ref = parent
    return None


def selector_string(selector):
    if not isinstance(selector, dict):
        return ""
    requirements = [f"{key}={value}"
                    for key, value in (selector.get("matchLabels") or {}).items()]
    for expression in selector.get("matchExpressions", []):
        key = expression.get("key")
        values = expression.get("values", [])
        operator = expression.get("operator")
        if not key:
            return ""
        if operator == "In" and values:
            requirements.append(f"{key} in ({','.join(values)})")
        elif operator == "NotIn" and values:
            requirements.append(f"{key} notin ({','.join(values)})")
        elif operator == "Exists":
            requirements.append(key)
        elif operator == "DoesNotExist":
            requirements.append(f"!{key}")
        elif operator in ("Equals", "DoubleEquals") and len(values) == 1:
            requirements.append(f"{key}={values[0]}")
        elif operator == "NotEquals" and len(values) == 1:
            requirements.append(f"{key}!={values[0]}")
        else:
            return ""
    return ",".join(requirements)


def reconcile(api):
    jobs = api("/apis/scheduling.koordinator.sh/v1alpha1/podmigrationjobs")["items"]
    now = datetime.now(timezone.utc)
    for job in jobs:
        status = job.get("status", {})
        if status.get("phase") != "Failed":
            continue
        if job["metadata"].get("annotations", {}).get("koordinator.sh/evict-trigger") != "CustomPriority":
            continue
        spec = job["spec"]
        namespace = spec["podRef"]["namespace"]
        template = (spec.get("reservationOptions", {}).get("template", {})
                    .get("spec", {}).get("template", {}))
        ref = owner(template)
        if ref is None:
            pod = api(f"/api/v1/namespaces/{namespace}/pods/{spec['podRef']['name']}")
            if pod is None or pod["metadata"]["uid"] != spec["podRef"].get("uid"):
                continue
            ref = owner(pod)
        target = workload(api, namespace, ref)
        if target is None:
            continue
        endpoint, obj = target
        annotations = obj["metadata"].get("annotations", {})
        previous = failure_record(annotations.get(FAILURE))
        if previous.get("job") == job["metadata"]["name"]:
            continue
        previous_time = parse_time(previous.get("observedAt"))
        try:
            previous_failures = int(previous.get("consecutiveFailures", 0))
        except (TypeError, ValueError):
            previous_failures = 0
        if previous_time and (now - previous_time).total_seconds() < RETRY_RESET_SECONDS:
            consecutive_failures = max(previous_failures, 1) + 1
        else:
            consecutive_failures = 1
        delay = min(RETRY_BASE_SECONDS * (2 ** min(consecutive_failures - 1, 20)), RETRY_MAX_SECONDS)
        record = json.dumps({
            "job": job["metadata"]["name"],
            "reason": status.get("reason"),
            "message": status.get("message"),
            "observedAt": now.isoformat().replace("+00:00", "Z"),
            "retryAfter": (now + timedelta(seconds=delay)).isoformat().replace("+00:00", "Z"),
            "consecutiveFailures": consecutive_failures,
        }, sort_keys=True)
        api(endpoint, {"metadata": {"resourceVersion": obj["metadata"]["resourceVersion"],
                                     "labels": {BLOCK: "true"},
                                     "annotations": {FAILURE: record}}})
        print(f"Blocked {namespace}/{obj['kind']}/{obj['metadata']['name']} until "
              f"{retry_deadline(failure_record(record)).isoformat()}: {record}", flush=True)

    for kind in RESOURCES:
        query = urlencode({"labelSelector": f"{BLOCK}=true"})
        for obj in api(f"{path(kind)}?{query}")["items"]:
            meta = obj["metadata"]
            namespace = meta["namespace"]
            annotations = meta.get("annotations", {})
            deadline = retry_deadline(failure_record(annotations.get(FAILURE)))
            should_block = deadline is None or now < deadline
            is_blocked = meta.get("labels", {}).get(BLOCK) == "true"
            endpoint = path(kind, namespace, meta["name"])
            if is_blocked and not should_block:
                api(endpoint, {"metadata": {"resourceVersion": meta["resourceVersion"],
                                              "labels": {BLOCK: None}}})
                print(f"Retry enabled for {namespace}/{kind}/{meta['name']}", flush=True)

            selectors = []
            if kind == "CronJob":
                for label in ("batch.kubernetes.io/cronjob-name", "cronjob-name"):
                    jobs_query = urlencode({"labelSelector": f"{label}={meta['name']}"})
                    cron_jobs = api(f"{path('Job', namespace)}?{jobs_query}")["items"]
                    if cron_jobs:
                        selectors.extend(item.get("spec", {}).get("selector") for item in cron_jobs)
                        break
            else:
                selectors.append(obj.get("spec", {}).get("selector"))

            seen_pods = set()
            for selector in selectors:
                label_selector = selector_string(selector)
                if not label_selector:
                    continue
                pods_query = urlencode({"labelSelector": label_selector})
                pods = api(f"/api/v1/namespaces/{quote(namespace, safe='')}/pods?{pods_query}")["items"]
                for pod in pods:
                    pod_meta = pod["metadata"]
                    if pod_meta["uid"] in seen_pods or pod_meta.get("deletionTimestamp"):
                        continue
                    seen_pods.add(pod_meta["uid"])
                    resolved = workload(api, namespace, owner(pod))
                    if resolved is None or resolved[1]["metadata"]["uid"] != meta["uid"]:
                        continue
                    pod_blocked = pod_meta.get("labels", {}).get(BLOCK) == "true"
                    if pod_blocked == should_block:
                        continue
                    api(f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/"
                        f"{quote(pod_meta['name'], safe='')}",
                        {"metadata": {"resourceVersion": pod_meta["resourceVersion"],
                                      "labels": {BLOCK: "true" if should_block else None}}})
                    print(f"Pod {namespace}/{pod_meta['name']} blocked={should_block}", flush=True)


if __name__ == "__main__":
    api = API()
    while True:
        try:
            reconcile(api)
        except Exception as error:
            print(f"Reconcile failed: {type(error).__name__}: {error}", flush=True)
        time.sleep(int(os.environ.get("POLL_SECONDS", "10")))
