import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from kubernetes import client

from pycalrissian.execution import CalrissianExecution, JobStatus

START = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)


def job_status(conditions=None, **kwargs):
    return client.V1Job(status=client.V1JobStatus(conditions=conditions, **kwargs))


def condition(type_, status="True"):
    return client.V1JobCondition(type=type_, status=status)


class TestExecutionStatus(unittest.TestCase):
    def setUp(self):
        self.context = Mock(namespace="ns")
        self.execution = CalrissianExecution(
            job=Mock(job_name="j"), runtime_context=self.context
        )
        self.execution.namespaced_job_name = "j"

    def set_job(self, job):
        self.context.batch_v1_api.read_namespaced_job_status.return_value = job

    def test_no_conditions_is_active(self):
        self.set_job(job_status())
        self.assertEqual(self.execution.get_status(), JobStatus.ACTIVE)

    def test_job_between_retries_is_active(self):
        self.set_job(job_status(failed=1, active=None, start_time=START))
        self.assertEqual(self.execution.get_status(), JobStatus.ACTIVE)

    def test_complete_is_succeeded(self):
        self.set_job(job_status([condition("Complete")], succeeded=1))
        self.assertEqual(self.execution.get_status(), JobStatus.SUCCEEDED)

    def test_failed_is_failed(self):
        self.set_job(job_status([condition("Failed")], failed=1))
        self.assertEqual(self.execution.get_status(), JobStatus.FAILED)

    def test_failure_target_only_is_active(self):
        self.set_job(job_status([condition("FailureTarget")], failed=1))
        self.assertEqual(self.execution.get_status(), JobStatus.ACTIVE)

    def test_failed_false_is_active(self):
        self.set_job(job_status([condition("Failed", "False")]))
        self.assertEqual(self.execution.get_status(), JobStatus.ACTIVE)

    def test_killed_does_not_call_api(self):
        self.execution.killed = True
        self.assertEqual(self.execution.get_status(), JobStatus.KILLED)
        self.context.batch_v1_api.read_namespaced_job_status.assert_not_called()

    def test_get_output_on_failed_job(self):
        self.set_job(job_status([condition("Failed")]))
        with patch("pycalrissian.execution.copy_from_volume") as copy:
            self.assertIsNone(self.execution.get_output())
        copy.assert_not_called()

    def test_get_log_while_active(self):
        self.set_job(job_status())
        self.assertIsNone(self.execution.get_log())
        self.context.core_v1_api.list_namespaced_pod.assert_not_called()

    def test_get_tool_logs_without_report(self):
        self.set_job(job_status())
        self.assertIsNone(self.execution.get_tool_logs())

    def test_get_log_reads_newest_pod(self):
        self.set_job(job_status([condition("Failed")]))
        old = client.V1Pod(
            metadata=client.V1ObjectMeta(name="old", creation_timestamp=START)
        )
        new = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name="new", creation_timestamp=START.replace(minute=5)
            )
        )
        core = self.context.core_v1_api
        core.list_namespaced_pod.return_value = client.V1PodList(items=[new, old])
        core.read_namespaced_pod_log.return_value.data = b"log"
        self.assertEqual(self.execution.get_log(), "log")
        self.assertEqual(core.read_namespaced_pod_log.call_args.kwargs["name"], "new")


if __name__ == "__main__":
    unittest.main()
