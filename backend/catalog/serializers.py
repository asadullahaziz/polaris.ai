"""catalog DRF serializers — listings (list + multi-property detail), create, mandate."""

from __future__ import annotations

from rest_framework import serializers

from .models import BUNDLE_TYPES, LISTING_STATUSES, MEDIA_KINDS, Listing, ListingMedia, Property


class PropertySerializer(serializers.ModelSerializer):
    """Read-only property view (matched properties are shown, never edited here)."""

    class Meta:
        model = Property
        fields = [
            "id",
            "address_raw",
            "property_type",
            "beds",
            "baths",
            "sqft",
            "lot_size_sqft",
            "year_built",
            "condition",
            "grade",
            "waterfront",
        ]
        read_only_fields = fields


class ListingMediaSerializer(serializers.ModelSerializer):
    class Meta:
        model = ListingMedia
        fields = ["id", "kind", "url", "sort_order"]
        read_only_fields = fields


class ListingPropertySerializer(serializers.Serializer):
    """A property as it appears IN a listing: the shared Property + per-listing price."""

    property = PropertySerializer(read_only=True)
    asking_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    sort_order = serializers.IntegerField()


class MandateSerializer(serializers.Serializer):
    """Writable mandate (deal-settings) fields. Target listing is set by the route."""

    floor_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    ceiling_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    must_haves = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    availability_window = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    instructions = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class ListingSummarySerializer(serializers.ModelSerializer):
    """The `/listings` card view: headline fields + primary property + cover photo."""

    primary_property = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            "id",
            "title",
            "status",
            "bundle_type",
            "asking_price",
            "created_at",
            "primary_property",
            "cover_url",
        ]
        read_only_fields = fields

    def _first_lp(self, obj):
        return sorted(obj.listingproperty_set.all(), key=lambda lp: lp.sort_order)[:1]

    def get_primary_property(self, obj):
        lps = self._first_lp(obj)
        return PropertySerializer(lps[0].property).data if lps else None

    def get_cover_url(self, obj):
        photos = sorted(
            (m for m in obj.media.all() if m.kind == "photo"), key=lambda m: m.sort_order
        )
        return photos[0].url if photos else None


class ListingDetailSerializer(serializers.ModelSerializer):
    """The `/listings/[id]` detail: every property + media + the deal mandate."""

    properties = serializers.SerializerMethodField()
    media = ListingMediaSerializer(many=True, read_only=True)
    mandate = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = [
            "id",
            "title",
            "description",
            "status",
            "bundle_type",
            "asking_price",
            "created_at",
            "updated_at",
            "properties",
            "media",
            "mandate",
        ]
        read_only_fields = fields

    def get_properties(self, obj):
        lps = sorted(obj.listingproperty_set.all(), key=lambda lp: lp.sort_order)
        return ListingPropertySerializer(lps, many=True).data

    def get_mandate(self, obj):
        from .services import get_mandate_for_listing

        return get_mandate_for_listing(obj)


class PropertyItemSerializer(serializers.Serializer):
    """One entry in a listing's property list: a match (`property_id`) OR new fields."""

    property_id = serializers.IntegerField(required=False, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True)
    property_type = serializers.CharField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    baths = serializers.FloatField(required=False, allow_null=True)
    sqft = serializers.IntegerField(required=False, allow_null=True)
    lot_size_sqft = serializers.IntegerField(required=False, allow_null=True)
    year_built = serializers.IntegerField(required=False, allow_null=True)
    condition = serializers.IntegerField(required=False, allow_null=True)
    grade = serializers.IntegerField(required=False, allow_null=True)
    waterfront = serializers.BooleanField(required=False, allow_null=True)
    asking_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    sort_order = serializers.IntegerField(required=False)

    def validate(self, attrs):
        if not attrs.get("property_id") and not (attrs.get("address") or "").strip():
            raise serializers.ValidationError(
                "each property needs a `property_id` (match) or an `address` (new)."
            )
        return attrs


class MediaItemSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=[k[0] for k in MEDIA_KINDS], default="photo")
    url = serializers.CharField()
    sort_order = serializers.IntegerField(required=False)


class ListingCreateSerializer(serializers.Serializer):
    """Create payload: listing fields + a property list (multi-property) + optional
    media + optional deal-settings mandate."""

    title = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    asking_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    bundle_type = serializers.ChoiceField(choices=[b[0] for b in BUNDLE_TYPES], default="single")
    status = serializers.ChoiceField(
        choices=[s[0] for s in LISTING_STATUSES], required=False, default="active"
    )
    properties = PropertyItemSerializer(many=True)
    media = MediaItemSerializer(many=True, required=False)
    mandate = MandateSerializer(required=False)

    def validate_properties(self, value):
        if not value:
            raise serializers.ValidationError("a listing needs at least one property.")
        return value


class ListingUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    description = serializers.CharField(required=False, allow_blank=True)
    asking_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, allow_null=True
    )
    bundle_type = serializers.ChoiceField(choices=[b[0] for b in BUNDLE_TYPES], required=False)
    status = serializers.ChoiceField(choices=[s[0] for s in LISTING_STATUSES], required=False)
    mandate = MandateSerializer(required=False)
