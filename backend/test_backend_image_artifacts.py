from pathlib import Path
import unittest


class BackendImageArtifactsTest(unittest.TestCase):
    def test_backend_image_includes_runtime_deployment_manifests(self):
        dockerfile = (Path(__file__).resolve().parents[1] / "deploy/docker/backend.Dockerfile").read_text()
        self.assertIn("COPY deploy ./deploy", dockerfile)
