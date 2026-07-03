from rest_framework import serializers

from .models import Listing, Property


class PropertySerializer(serializers.ModelSerializer):
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
        ]
        read_only_fields = fields


class ListingSerializer(serializers.ModelSerializer):
    property = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = ["id", "status", "bundle_type", "asking_price", "created_at", "property"]
        read_only_fields = fields

    def get_property(self, obj):
        lp = obj.listingproperty_set.first()
        return PropertySerializer(lp.property).data if lp else None


class ListingIntakeSerializer(serializers.Serializer):
    """Flat intake payload → Property + Listing + ListingProperty (single bundle)."""

    address = serializers.CharField()
    property_type = serializers.CharField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    baths = serializers.FloatField(required=False, allow_null=True)
    sqft = serializers.IntegerField(required=False, allow_null=True)
    lot_size_sqft = serializers.IntegerField(required=False, allow_null=True)
    year_built = serializers.IntegerField(required=False, allow_null=True)
    condition = serializers.CharField(required=False, allow_null=True)  # turnkey|cosmetic|full_gut
    asking_price = serializers.FloatField(required=False, allow_null=True)
