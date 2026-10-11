import copy
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("guard", pathlib.Path(__file__).with_name("migration-failure-guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def obj(kind, name, uid, parent=None, labels=None):
    metadata = {"name": name, "namespace": "redis", "uid": uid,
                "resourceVersion": "1", "labels": labels or {}}
    if parent:
        metadata["ownerReferences"] = [dict(parent, controller=True)]
    return {"kind": kind, "metadata": metadata}


def ref(kind, name, uid):
    return {"kind": kind, "name": name, "uid": uid}


class FakeAPI:
    def __init__(self):
        self.objects = {}
        self.jobs = []
        self.pods = []

    def __call__(self, endpoint, patch=None):
        if patch is not None:
            target = self.objects.get(endpoint)
            if target is None:
                target = next(p for p in self.pods if endpoint.endswith("/" + p["metadata"]["name"]))
            for key, values in patch["metadata"].items():
                if key == "resourceVersion":
                    continue
                for name, value in values.items():
                    if value is None:
                        target["metadata"].setdefault(key, {}).pop(name, None)
                    else:
                        target["metadata"].setdefault(key, {})[name] = value
            return copy.deepcopy(target)
        if endpoint.endswith("/podmigrationjobs"):
            return {"items": copy.deepcopy(self.jobs)}
        if endpoint == "/api/v1/pods":
            return {"items": copy.deepcopy(self.pods)}
        if endpoint in self.objects:
            return copy.deepcopy(self.objects[endpoint])
        return {"items": [copy.deepcopy(o) for p, o in self.objects.items()
                          if p.split("/")[-2] == endpoint.split("/")[-1]]} if "/namespaces/" not in endpoint else None


class FailureGuardTest(unittest.TestCase):
    def setUp(self):
        self.api = FakeAPI()
        self.deployment = obj("Deployment", "haproxy", "deployment")
        self.rs = obj("ReplicaSet", "haproxy-old", "rs-old", ref("Deployment", "haproxy", "deployment"))
        self.api.objects[guard.path("Deployment", "redis", "haproxy")] = self.deployment
        self.api.objects[guard.path("ReplicaSet", "redis", "haproxy-old")] = self.rs
        self.pod = obj("Pod", "replacement", "pod", ref("ReplicaSet", "haproxy-old", "rs-old"))
        self.api.pods.append(self.pod)
        self.api.jobs.append({"metadata": {"name": "failed-migration", "annotations": {
            "koordinator.sh/evict-trigger": "CustomPriority"}},
            "spec": {"podRef": {"namespace": "redis", "name": "deleted-pod", "uid": "gone"},
                     "reservationOptions": {"template": {"spec": {"template": {
                         "metadata": {"ownerReferences": [dict(ref("ReplicaSet", "haproxy-old", "rs-old"), controller=True)]}}}}}},
            "status": {"phase": "Failed", "reason": "Timeout"}})

    def test_failure_blocks_replacements_and_later_rollouts_without_changing_templates(self):
        guard.reconcile(self.api)
        self.assertEqual(self.deployment["metadata"]["labels"][guard.BLOCK], "true")
        self.assertEqual(self.pod["metadata"]["labels"][guard.BLOCK], "true")
        self.assertNotIn("spec", self.deployment)
        self.api.jobs = []  # Job retention and guard restart do not clear the block.
        new_rs = obj("ReplicaSet", "haproxy-new", "rs-new", ref("Deployment", "haproxy", "deployment"))
        self.api.objects[guard.path("ReplicaSet", "redis", "haproxy-new")] = new_rs
        new_pod = obj("Pod", "new-rollout", "new", ref("ReplicaSet", "haproxy-new", "rs-new"))
        self.api.pods.append(new_pod)
        guard.reconcile(self.api)
        self.assertEqual(new_pod["metadata"]["labels"][guard.BLOCK], "true")

    def test_running_and_other_trigger_do_not_block(self):
        self.api.jobs[0]["status"]["phase"] = "Running"
        guard.reconcile(self.api)
        self.assertNotIn(guard.BLOCK, self.deployment["metadata"]["labels"])
        self.api.jobs[0]["status"]["phase"] = "Failed"
        self.api.jobs[0]["metadata"]["annotations"]["koordinator.sh/evict-trigger"] = "RemoveDuplicates"
        guard.reconcile(self.api)
        self.assertNotIn(guard.BLOCK, self.deployment["metadata"]["labels"])

    def test_recreated_controller_uid_is_not_blocked(self):
        self.deployment["metadata"]["uid"] = "different-deployment"
        guard.reconcile(self.api)
        self.assertNotIn(guard.BLOCK, self.deployment["metadata"]["labels"])

    def test_explicit_unblock_clears_pod_label_after_failure_job_is_gone(self):
        guard.reconcile(self.api)
        self.api.jobs = []
        self.deployment["metadata"]["labels"].pop(guard.BLOCK)
        guard.reconcile(self.api)
        self.assertNotIn(guard.BLOCK, self.pod["metadata"]["labels"])


if __name__ == "__main__":
    unittest.main()
