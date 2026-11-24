import unittest
from unittest.mock import Mock

from pycalrissian.job import CalrissianJob


def make_job(**kwargs):
    context = Mock(
        namespace="ns",
        calrissian_wdir="wdir",
        service_account="sa",
        calling_service_account="calling-sa",
    )
    context.core_v1_api.read_namespaced_config_map.return_value.data = {"pvcs": "[]"}
    return CalrissianJob(
        cwl={"$graph": []},
        params={},
        runtime_context=context,
        calling_workspace="ws-a",
        executing_workspace="ws-a",
        job_id="job-1",
        **kwargs,
    )


class TestJobFailurePolicy(unittest.TestCase):
    def test_default_has_no_pod_failure_policy(self):
        job = make_job().to_k8s_job()
        self.assertEqual(job.spec.backoff_limit, 2)
        self.assertIsNone(job.spec.pod_failure_policy)

    def test_retry_on_disruption_only(self):
        job = make_job(backoff_limit=0, retry_on_disruption=True).to_k8s_job()
        self.assertEqual(job.spec.backoff_limit, 0)
        self.assertEqual(job.spec.template.spec.restart_policy, "Never")
        rules = job.spec.pod_failure_policy.rules
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].action, "Ignore")
        self.assertEqual(rules[0].on_pod_conditions[0].type, "DisruptionTarget")
        self.assertEqual(rules[0].on_pod_conditions[0].status, "True")


if __name__ == "__main__":
    unittest.main()
