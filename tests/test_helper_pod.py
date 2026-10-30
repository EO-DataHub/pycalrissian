import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

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
        self.core.delete_namespaced_pod.assert_called_once_with(pod_name, "ns")

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
