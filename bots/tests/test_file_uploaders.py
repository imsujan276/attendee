import tempfile
from unittest import mock

from django.test import SimpleTestCase

from bots.bot_controller.azure_file_uploader import AzureFileUploader
from bots.bot_controller.s3_file_uploader import S3FileUploader


class TestS3FileUploaderContentType(SimpleTestCase):
    @mock.patch("bots.bot_controller.s3_file_uploader.boto3.client")
    def _upload(self, filename, mock_boto3_client):
        uploader = S3FileUploader(bucket="test-bucket", filename=filename)
        with tempfile.NamedTemporaryFile() as file:
            uploader.upload_file(file.name)
            uploader.wait_for_upload()
        return mock_boto3_client.return_value.upload_file.call_args

    def test_recordings_are_uploaded_with_their_content_type(self):
        self.assertEqual(self._upload("bot_abc-rec_abc.mp4").kwargs["ExtraArgs"], {"ContentType": "video/mp4"})
        self.assertEqual(self._upload("bot_abc-rec_abc.webm").kwargs["ExtraArgs"], {"ContentType": "video/webm"})
        self.assertEqual(self._upload("bot_abc-rec_abc-audio.mp3").kwargs["ExtraArgs"], {"ContentType": "audio/mpeg"})

    def test_unknown_extensions_are_uploaded_without_a_content_type(self):
        self.assertIsNone(self._upload("bot_abc-rec_abc.unknownext").kwargs["ExtraArgs"])


class TestAzureFileUploaderContentType(SimpleTestCase):
    @mock.patch("bots.bot_controller.azure_file_uploader.BlobServiceClient.from_connection_string")
    def _upload(self, filename, mock_from_connection_string):
        uploader = AzureFileUploader(container="test-container", filename=filename, connection_string="test-connection-string", account_key=None, account_name=None)
        uploader.blob_client.url = f"https://test.blob.core.windows.net/test-container/{filename}"
        with tempfile.NamedTemporaryFile() as file:
            uploader.upload_file(file.name)
            uploader.wait_for_upload()
        return mock_from_connection_string.return_value.get_blob_client.return_value.upload_blob.call_args

    def test_recordings_are_uploaded_with_their_content_type(self):
        self.assertEqual(self._upload("bot_abc-rec_abc.mp4").kwargs["content_settings"].content_type, "video/mp4")
        self.assertEqual(self._upload("bot_abc-rec_abc-audio.mp3").kwargs["content_settings"].content_type, "audio/mpeg")

    def test_unknown_extensions_are_uploaded_without_a_content_type(self):
        self.assertIsNone(self._upload("bot_abc-rec_abc.unknownext").kwargs["content_settings"])
