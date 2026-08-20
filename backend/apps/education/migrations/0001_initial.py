import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='LearningPath',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('slug', models.SlugField(blank=True, max_length=220, unique=True)),
                ('summary', models.CharField(blank=True, max_length=300)),
                ('industry', models.CharField(blank=True, max_length=120)),
                ('icon', models.CharField(blank=True, max_length=8)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('review', 'In review'), ('published', 'Published'), ('archived', 'Archived')], default='draft', max_length=12)),
                ('order', models.PositiveSmallIntegerField(default=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['order', 'title'],
            },
        ),
        migrations.CreateModel(
            name='ResourceCategory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=120)),
                ('slug', models.SlugField(blank=True, max_length=140, unique=True)),
                ('description', models.CharField(blank=True, max_length=255)),
                ('icon', models.CharField(blank=True, max_length=8)),
                ('order', models.PositiveSmallIntegerField(default=100)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name_plural': 'Resource categories',
                'ordering': ['order', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Resource',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('kind', models.CharField(choices=[('article', 'Article'), ('guide', 'Guide'), ('lesson', 'Lesson'), ('course', 'Course'), ('template', 'Template'), ('calculator', 'Calculator'), ('checklist', 'Checklist'), ('video', 'Video')], default='article', max_length=16)),
                ('title', models.CharField(max_length=200)),
                ('slug', models.SlugField(blank=True, max_length=220, unique=True)),
                ('summary', models.CharField(blank=True, max_length=300)),
                ('body', models.TextField(blank=True)),
                ('industry', models.CharField(blank=True, max_length=120)),
                ('company_size', models.CharField(blank=True, max_length=40)),
                ('difficulty', models.CharField(choices=[('beginner', 'Beginner'), ('intermediate', 'Intermediate'), ('advanced', 'Advanced')], default='beginner', max_length=16)),
                ('read_minutes', models.PositiveSmallIntegerField(default=4)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('review', 'In review'), ('published', 'Published'), ('archived', 'Archived')], default='draft', max_length=12)),
                ('is_featured', models.BooleanField(default=False)),
                ('related_features', models.JSONField(blank=True, default=list)),
                ('cta_label', models.CharField(blank=True, max_length=80)),
                ('cta_url', models.CharField(blank=True, max_length=300)),
                ('seo_title', models.CharField(blank=True, max_length=200)),
                ('seo_description', models.CharField(blank=True, max_length=300)),
                ('featured_image_url', models.URLField(blank=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resources', to='education.resourcecategory')),
            ],
            options={
                'ordering': ['-published_at', '-created_at'],
            },
        ),
        migrations.CreateModel(
            name='LearningPathStep',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('title', models.CharField(blank=True, max_length=200)),
                ('description', models.CharField(blank=True, max_length=300)),
                ('path', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='education.learningpath')),
                ('resource', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='education.resource')),
            ],
            options={
                'ordering': ['path', 'order'],
            },
        ),
        migrations.AddIndex(
            model_name='resource',
            index=models.Index(fields=['status', 'kind'], name='education_r_status_4cff47_idx'),
        ),
        migrations.AddIndex(
            model_name='resource',
            index=models.Index(fields=['status', 'is_featured'], name='education_r_status_b6fbcd_idx'),
        ),
    ]
