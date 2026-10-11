"""Persist CustomPriority migration failures and exclude their workload's pods."""

import json
import os
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

BLOCK = "koordinator.chik4ge.me/migration-blocked"
FAILURE = "koordinator.chik4ge.me/migration-failure"
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


def reconcile(api):
    jobs = api("/apis/scheduling.koordinator.sh/v1alpha1/podmigrationjobs")["items"]
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
        if obj["metadata"].get("labels", {}).get(BLOCK) == "true":
            continue
        record = json.dumps({"job": job["metadata"]["name"],
                             "reason": status.get("reason"),
                             "message": status.get("message"),
                             "observedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        api(endpoint, {"metadata": {"resourceVersion": obj["metadata"]["resourceVersion"],
                                     "labels": {BLOCK: "true"},
                                     "annotations": {FAILURE: record}}})
        print(f"Blocked {namespace}/{obj['kind']}/{obj['metadata']['name']}: {record}", flush=True)

    blocked = set()
    parents = {}
    # Build owner links, including new ReplicaSets created by later rollouts.
    for kind in RESOURCES:
        for obj in api(path(kind))["items"]:
            uid = obj["metadata"]["uid"]
            ref = owner(obj)
            if ref:
                parents[uid] = ref["uid"]
            if obj["metadata"].get("labels", {}).get(BLOCK) == "true":
                blocked.add(uid)

    for pod in api("/api/v1/pods")["items"]:
        ref = owner(pod)
        uid = ref["uid"] if ref else None
        seen = set()
        while uid and uid not in blocked and uid not in seen:
            seen.add(uid)
            uid = parents.get(uid)
        should_block = uid in blocked
        is_blocked = pod["metadata"].get("labels", {}).get(BLOCK) == "true"
        if should_block == is_blocked or pod["metadata"].get("deletionTimestamp"):
            continue
        meta = pod["metadata"]
        # resourceVersion protects against patching a replacement with the same name.
        api(f"/api/v1/namespaces/{meta['namespace']}/pods/{meta['name']}",
            {"metadata": {"resourceVersion": meta["resourceVersion"],
                          "labels": {BLOCK: "true" if should_block else None}}})
        print(f"Pod {meta['namespace']}/{meta['name']} blocked={should_block}", flush=True)


if __name__ == "__main__":
    api = API()
    while True:
        try:
            reconcile(api)
        except Exception as error:
            print(f"Reconcile failed: {type(error).__name__}: {error}", flush=True)
        time.sleep(int(os.environ.get("POLL_SECONDS", "5")))
