import logging
import mimetypes

from django.conf import settings
from django.core.management.base import BaseCommand

from bots.models import Recording

logger = logging.getLogger(__name__)

GENERIC_CONTENT_TYPES = ["binary/octet-stream", "application/octet-stream", ""]
RECORDING_FILE_FIELDS = ["file", "audio_only_file", "video_only_file"]


class Command(BaseCommand):
    help = "Sets the content type of recording files that were uploaded without one (binary/octet-stream), so browsers play them instead of downloading them. Only the stored file's metadata is changed, the file itself is not re-uploaded."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List the files that would be fixed without changing them")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        fixed_count = 0

        for recording in Recording.objects.exclude(file="").iterator():
            for field_name in RECORDING_FILE_FIELDS:
                file_field = getattr(recording, field_name)
                if not file_field.name:
                    continue

                content_type, _ = mimetypes.guess_type(file_field.name)
                if not content_type:
                    continue

                try:
                    current_content_type = self.get_content_type(file_field)
                    if current_content_type not in GENERIC_CONTENT_TYPES:
                        continue

                    self.stdout.write(f"{'Would set' if dry_run else 'Setting'} the content type of {file_field.name} from {current_content_type or 'none'} to {content_type}")
                    if not dry_run:
                        self.set_content_type(file_field, content_type)
                    fixed_count += 1
                except Exception as e:
                    logger.exception(f"Could not fix the content type of {file_field.name}: {e}")

        self.stdout.write(f"{'Would fix' if dry_run else 'Fixed'} {fixed_count} recording files")

    def get_content_type(self, file_field):
        if settings.STORAGE_PROTOCOL == "azure":
            return file_field.storage.client.get_blob_client(file_field.name).get_blob_properties().content_settings.content_type or ""
        return file_field.storage.bucket.meta.client.head_object(Bucket=file_field.storage.bucket_name, Key=file_field.name).get("ContentType", "")

    def set_content_type(self, file_field, content_type):
        if settings.STORAGE_PROTOCOL == "azure":
            from azure.storage.blob import ContentSettings

            file_field.storage.client.get_blob_client(file_field.name).set_http_headers(content_settings=ContentSettings(content_type=content_type))
            return

        # S3 can't change an object's metadata in place, so the object is copied onto itself with the new content type
        client = file_field.storage.bucket.meta.client
        bucket = file_field.storage.bucket_name
        existing_metadata = client.head_object(Bucket=bucket, Key=file_field.name).get("Metadata", {})
        client.copy_object(
            Bucket=bucket,
            Key=file_field.name,
            CopySource={"Bucket": bucket, "Key": file_field.name},
            MetadataDirective="REPLACE",
            ContentType=content_type,
            Metadata=existing_metadata,
        )
