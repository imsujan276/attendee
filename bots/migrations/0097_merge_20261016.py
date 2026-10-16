# Merges the upstream participant event migration with this branch's migrations

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0095_alter_participantevent_event_type_and_more'),
        ('bots', '0096_project_active_transcription_provider'),
    ]

    operations = []
