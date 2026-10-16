from unittest import mock

from django.test import SimpleTestCase, override_settings

from bots.storage import remote_storage_url


def s3_file_field(querystring_auth):
    storage = mock.Mock(querystring_auth=querystring_auth, bucket_name="test-bucket")
    storage.url.return_value = "https://test-bucket.s3.amazonaws.com/bot_abc-rec_abc.mp4"
    storage.bucket.meta.client.generate_presigned_url.return_value = "https://test-bucket.s3.amazonaws.com/bot_abc-rec_abc.mp4?X-Amz-Signature=abc"
    file_field = mock.Mock(storage=storage)
    file_field.name = "bot_abc-rec_abc.mp4"
    return file_field


@override_settings(STORAGE_PROTOCOL="s3")
class TestRemoteStorageUrl(SimpleTestCase):
    def test_signed_url_that_expires_by_default(self):
        file_field = s3_file_field(querystring_auth=True)

        self.assertEqual(remote_storage_url(file_field), "https://test-bucket.s3.amazonaws.com/bot_abc-rec_abc.mp4?X-Amz-Signature=abc")
        file_field.storage.bucket.meta.client.generate_presigned_url.assert_called_once_with("get_object", Params={"Bucket": "test-bucket", "Key": "bot_abc-rec_abc.mp4"}, ExpiresIn=1800)

    def test_plain_url_when_the_storage_uses_permanent_links(self):
        file_field = s3_file_field(querystring_auth=False)

        self.assertEqual(remote_storage_url(file_field), "https://test-bucket.s3.amazonaws.com/bot_abc-rec_abc.mp4")
        file_field.storage.url.assert_called_once_with("bot_abc-rec_abc.mp4")
        file_field.storage.bucket.meta.client.generate_presigned_url.assert_not_called()
