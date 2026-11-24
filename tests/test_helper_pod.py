import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, Mock, patch

from kubernetes import client
from kubernetes.client.rest import ApiException

from pycalrissian.utils import HelperPod, HelperPodError, copy_from_volume

VOLUME = {"name": "wdir", "persistentVolumeClaim": {"claimName": "pvc-1"}}
VOLUME_MOUNT = {"name": "wdir", "mountPath": "/calrissian"}


def pod(phase):
    return client.V1Pod(status=client.V1PodStatus(phase=phase))


def pvc(deletion_timestamp=None):
    return client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(
            name="pvc-1", deletion_timestamp=deletion_timestamp
        )
    )


@patch("pycalrissian.utils.time.sleep")
class TestHelperPod(unittest.TestCase):
    def setUp(self):
        self.context = Mock(namespace="ns")
        self.core = self.context.core_v1_api
        self.core.read_namespaced_persistent_volume_claim.return_value = pvc()

    def helper_pod(self, volume=VOLUME, timeout=300):
        return HelperPod(
            context=self.context,
            volume=volume,
            volume_mount=VOLUME_MOUNT,
            timeout=timeout,
        )

    def test_missing_pvc(self, _sleep):
        self.core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
            status=404
        )
        with self.assertRaisesRegex(HelperPodError, "pvc pvc-1 not found"):
            self.helper_pod()
        self.core.create_namespaced_pod.assert_not_called()

    def test_pvc_read_error_is_raised(self, _sleep):
        self.core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
            status=403
        )
        with self.assertRaises(ApiException):
            self.helper_pod()
        self.core.create_namespaced_pod.assert_not_called()

    def test_pvc_being_deleted(self, _sleep):
        self.core.read_namespaced_persistent_volume_claim.return_value = pvc(
            datetime(2026, 10, 22, tzinfo=timezone.utc)
        )
        with self.assertRaisesRegex(HelperPodError, "being deleted"):
            self.helper_pod()
        self.core.create_namespaced_pod.assert_not_called()

    def test_pending_past_timeout(self, _sleep):
        self.core.read_namespaced_pod.return_value = pod("Pending")
        with self.assertRaisesRegex(HelperPodError, "still Pending after 0s"):
            self.helper_pod(timeout=0)
        pod_name = self.core.create_namespaced_pod.call_args.kwargs["body"]["metadata"][
            "name"
        ]
        self.core.delete_namespaced_pod.assert_called_once_with(
            pod_name, "ns", _request_timeout=ANY
        )

    def test_running(self, _sleep):
        self.core.read_namespaced_pod.side_effect = [pod("Pending"), pod("Running")]
        self.helper_pod()
        self.core.delete_namespaced_pod.assert_not_called()

    def test_failed(self, _sleep):
        self.core.read_namespaced_pod.return_value = pod("Failed")
        with self.assertRaisesRegex(HelperPodError, "is Failed"):
            self.helper_pod()
        self.core.delete_namespaced_pod.assert_called_once()

    def test_volume_without_pvc(self, _sleep):
        self.core.read_namespaced_pod.return_value = pod("Running")
        self.helper_pod(volume={"name": "wdir", "emptyDir": {}})
        self.core.read_namespaced_persistent_volume_claim.assert_not_called()

    def test_create_error_still_deletes_pod(self, _sleep):
        error = ConnectionError("response lost")
        self.core.create_namespaced_pod.side_effect = error
        with self.assertRaises(ConnectionError) as raised:
            self.helper_pod()
        self.assertIs(raised.exception, error)
        self.core.delete_namespaced_pod.assert_called_once()

    def test_failed_cleanup_keeps_original_error(self, _sleep):
        self.core.create_namespaced_pod.side_effect = ConnectionError("create")
        self.core.delete_namespaced_pod.side_effect = ConnectionError("delete")
        with self.assertRaisesRegex(ConnectionError, "create"):
            self.helper_pod()

    def test_dismiss_ignores_missing_pod(self, _sleep):
        self.core.read_namespaced_pod.return_value = pod("Running")
        helper = self.helper_pod()
        self.core.delete_namespaced_pod.side_effect = ApiException(status=404)
        helper.dismiss()

    def test_api_calls_have_request_timeout(self, _sleep):
        self.core.read_namespaced_pod.return_value = pod("Failed")
        with self.assertRaises(HelperPodError):
            self.helper_pod()
        for call in (
            self.core.read_namespaced_persistent_volume_claim,
            self.core.create_namespaced_pod,
            self.core.read_namespaced_pod,
            self.core.delete_namespaced_pod,
        ):
            timeout = call.call_args.kwargs["_request_timeout"]
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 30)

    def test_delete_timeout_after_deadline(self, _sleep):
        self.core.read_namespaced_pod.return_value = pod("Pending")
        with self.assertRaises(HelperPodError):
            self.helper_pod(timeout=0)
        timeout = self.core.delete_namespaced_pod.call_args.kwargs["_request_timeout"]
        self.assertGreater(timeout, 0)

    @patch("pycalrissian.utils.time.monotonic")
    def test_slow_read_hits_deadline(self, monotonic, _sleep):
        clock = [0.0]
        monotonic.side_effect = lambda: clock[0]

        def slow_read(**kwargs):
            # each read takes as long as its timeout allows
            clock[0] += kwargs["_request_timeout"]
            return pod("Pending")

        self.core.read_namespaced_pod.side_effect = slow_read
        with self.assertRaisesRegex(HelperPodError, "still Pending after 60s"):
            self.helper_pod(timeout=60)
        self.assertEqual(self.core.read_namespaced_pod.call_count, 2)
        timeouts = [
            c.kwargs["_request_timeout"]
            for c in self.core.read_namespaced_pod.call_args_list
        ]
        self.assertEqual(timeouts, [30, 30])
        self.core.delete_namespaced_pod.assert_called_once()

    def test_copy_from_volume_raises(self, _sleep):
        self.core.read_namespaced_persistent_volume_claim.side_effect = ApiException(
            status=404
        )
        with self.assertRaises(HelperPodError):
            copy_from_volume(
                context=self.context,
                volume=VOLUME,
                volume_mount=VOLUME_MOUNT,
                source_paths=["/calrissian/output.json"],
                destination_path=".",
            )


if __name__ == "__main__":
    unittest.main()
