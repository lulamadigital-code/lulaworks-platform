from rest_framework import serializers

from .models import ImportBatch, ImportedDocument, StagedEntity


class ImportBatchSerializer(serializers.ModelSerializer):
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = ImportBatch
        fields = ["id", "label", "status", "status_display", "document_count",
                  "entity_count", "notes", "created_at"]
        read_only_fields = ["id", "status", "status_display", "document_count",
                            "entity_count", "created_at"]


class ImportedDocumentSerializer(serializers.ModelSerializer):
    doc_type_display = serializers.CharField(source="get_doc_type_display", read_only=True)

    class Meta:
        model = ImportedDocument
        fields = ["id", "filename", "doc_type", "doc_type_display",
                  "doc_type_confidence", "text_chars", "failed"]


class StagedEntitySerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    verdict_display = serializers.CharField(source="get_verdict_display", read_only=True)

    class Meta:
        model = StagedEntity
        fields = ["id", "kind", "kind_display", "raw_name", "email", "phone",
                  "reference", "verdict", "verdict_display", "confidence",
                  "match_id", "match_label", "review_status", "resolved_id",
                  "document"]
