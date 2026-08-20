import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('education', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='EducationLead',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('email', models.EmailField(max_length=254, unique=True)),
                ('name', models.CharField(blank=True, max_length=150)),
                ('company', models.CharField(blank=True, max_length=200)),
                ('industry', models.CharField(blank=True, max_length=120)),
                ('company_size', models.CharField(blank=True, max_length=40)),
                ('role', models.CharField(blank=True, max_length=120)),
                ('phone', models.CharField(blank=True, max_length=40)),
                ('challenge', models.TextField(blank=True)),
                ('first_source', models.CharField(blank=True, max_length=160)),
                ('score', models.PositiveIntegerField(default=0)),
                ('has_account', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['-score', '-updated_at'],
            },
        ),
        migrations.CreateModel(
            name='LeadEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event', models.CharField(max_length=40)),
                ('points', models.PositiveSmallIntegerField(default=0)),
                ('detail', models.CharField(blank=True, max_length=160)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('lead', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='events', to='education.educationlead')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
