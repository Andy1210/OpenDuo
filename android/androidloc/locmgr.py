"""The calls duo2-android-location makes into Android 13's system_server
(LineageOS 20, the Waydroid image), as transaction codes and argument
bytes (pure).

The codes are FIRST_CALL_TRANSACTION (1) + the method's place in the AIDL
file, from LineageOS's lineage-20.0 frameworks/base:

  location/java/android/location/ILocationManager.aidl (62 methods)
  core/java/com/android/internal/app/IAppOpsService.aidl (50 methods)
  location/java/android/location/provider/IProviderRequestListener.aidl

A wrong code would call another method, so the bridge first makes two
read-only calls whose answers it knows (hasProvider("passive") is true,
getAllProviders() lists "passive") and does nothing if they do not fit
(self_check_ok). The parcelables are written like their writeToParcel
(Location, ProviderProperties) and read like their createFromParcel
(ProviderRequest).

Who we are to Android: the host's GUI user (uid 1000) is AID_SYSTEM in the
container, the package "android". Test providers need the MOCK_LOCATION
app op for that package (checkOperation / setMode, once: Android keeps it).
"""

from .parcel import Writer

LOCATION_SERVICE = "location"
LOCATION_IFACE = "android.location.ILocationManager"
APPOPS_SERVICE = "appops"
APPOPS_IFACE = "com.android.internal.app.IAppOpsService"
LISTENER_IFACE = "android.location.provider.IProviderRequestListener"

# ILocationManager
ADD_PROVIDER_REQUEST_LISTENER = 30
REMOVE_PROVIDER_REQUEST_LISTENER = 31
HAS_PROVIDER = 36
GET_ALL_PROVIDERS = 37
IS_LOCATION_ENABLED_FOR_USER = 48
ADD_TEST_PROVIDER = 54
REMOVE_TEST_PROVIDER = 55
SET_TEST_PROVIDER_LOCATION = 56
SET_TEST_PROVIDER_ENABLED = 57
# IAppOpsService
CHECK_OPERATION = 1
SET_MODE = 31
# IProviderRequestListener (oneway)
ON_PROVIDER_REQUEST_CHANGED = 1

OP_MOCK_LOCATION = 58
MODE_ALLOWED = 0
SYSTEM_UID = 1000
PACKAGE = "android"

GPS = "gps"
NETWORK = "network"

POWER_USAGE_LOW, POWER_USAGE_HIGH = 1, 3
ACCURACY_FINE, ACCURACY_COARSE = 1, 2

INTERVAL_DISABLED = 2**63 - 1

# Location's field mask (Location.java)
HAS_ALTITUDE = 1 << 0
HAS_SPEED = 1 << 1
HAS_BEARING = 1 << 2
HAS_HORIZONTAL_ACCURACY = 1 << 3

# Parcel exception codes (the first int of a reply)
EXCEPTIONS = {
    -1: "SecurityException",
    -2: "BadParcelableException",
    -3: "IllegalArgumentException",
    -4: "NullPointerException",
    -5: "IllegalStateException",
    -6: "NetworkOnMainThreadException",
    -7: "UnsupportedOperationException",
    -8: "ServiceSpecificException",
    -9: "Parcelable",
}
EX_HAS_NOTED_APPOPS_REPLY_HEADER = -127
EX_HAS_STRICTMODE_REPLY_HEADER = -128


# -- arguments ------------------------------------------------------------------------


def provider_properties(name):
    """ProviderProperties for our test providers: the gps one like a GNSS
    receiver (satellites, altitude / speed / bearing, high power, fine),
    the network one like a network location provider."""
    w = Writer()
    gps = name == GPS
    w.boolean(not gps)  # hasNetworkRequirement
    w.boolean(gps)  # hasSatelliteRequirement
    w.boolean(False)  # hasCellRequirement
    w.boolean(False)  # hasMonetaryCost
    w.boolean(gps)  # hasAltitudeSupport
    w.boolean(gps)  # hasSpeedSupport
    w.boolean(gps)  # hasBearingSupport
    w.int32(POWER_USAGE_HIGH if gps else POWER_USAGE_LOW)
    w.int32(ACCURACY_FINE if gps else ACCURACY_COARSE)
    return w.bytes()


def add_test_provider(name):
    """addTestProvider(String name, in ProviderProperties, in List<String>
    locationTags, String packageName, @nullable String attributionTag).
    The tag list must not be null (the service copies it)."""
    w = Writer().string16(name).int32(1).raw(provider_properties(name))
    return w.string_list([]).string16(PACKAGE).string16(None).bytes()


def remove_test_provider(name):
    return Writer().string16(name).string16(PACKAGE).string16(None).bytes()


def set_test_provider_enabled(name, enabled):
    return Writer().string16(name).boolean(enabled).string16(PACKAGE).string16(None).bytes()


def has_provider(name):
    return Writer().string16(name).bytes()


def is_location_enabled_for_user(user=0):
    return Writer().int32(user).bytes()


def location(provider, fix, elapsed_realtime_ns):
    """A Location (Location.writeToParcel) for a geoclue fix: a dict with
    latitude, longitude, accuracy (m), time_ms (the fix's wall clock),
    and optionally altitude (m), speed (m/s), bearing (degrees).
    elapsed_realtime_ns is CLOCK_BOOTTIME (Android's elapsedRealtimeNanos:
    the container shares the host's kernel clock)."""
    mask = HAS_HORIZONTAL_ACCURACY
    alt, speed, bearing = fix.get("altitude"), fix.get("speed"), fix.get("bearing")
    if alt is not None:
        mask |= HAS_ALTITUDE
    if speed is not None:
        mask |= HAS_SPEED
    if bearing is not None:
        mask |= HAS_BEARING
    w = Writer().string8(provider).int32(mask).int64(fix["time_ms"]).int64(elapsed_realtime_ns)
    w.double(fix["latitude"]).double(fix["longitude"])
    if alt is not None:
        w.double(alt)
    if speed is not None:
        w.float32(speed)
    if bearing is not None:
        w.float32(bearing)
    w.float32(fix["accuracy"])
    w.int32(-1)  # extras: null Bundle
    return w.bytes()


def set_test_provider_location(provider, fix, elapsed_realtime_ns):
    w = Writer().string16(provider).int32(1).raw(location(provider, fix, elapsed_realtime_ns))
    return w.string16(PACKAGE).string16(None).bytes()


def check_operation():
    return Writer().int32(OP_MOCK_LOCATION).int32(SYSTEM_UID).string16(PACKAGE).bytes()


def set_mode_allowed():
    w = Writer().int32(OP_MOCK_LOCATION).int32(SYSTEM_UID).string16(PACKAGE)
    return w.int32(MODE_ALLOWED).bytes()


# -- replies --------------------------------------------------------------------------


class RemoteError(Exception):
    pass


def check_exception(reader):
    """The reply's exception header: nothing when the call went through,
    RemoteError (with Android's message) when it threw."""
    code = reader.read_int32()
    if code == EX_HAS_NOTED_APPOPS_REPLY_HEADER:
        # noted app ops of the caller: [count, (tag, n, longs...)...]; not
        # sent to the system uid in practice. Skip what we can.
        raise RemoteError("reply with noted app ops (not parsed)")
    if code == EX_HAS_STRICTMODE_REPLY_HEADER:
        # a "fat" reply header (StrictMode stacks), sent only with no
        # exception: its size counts from the size field on; the return
        # value follows it (Parcel.readExceptionCode)
        size = reader.read_int32()
        for _ in range(max(0, size // 4 - 1)):
            reader.read_int32()
        return
    if code == 0:
        return
    message = None
    try:
        message = reader.read_string16()
    except (ValueError, TypeError):
        pass
    raise RemoteError("%s: %s" % (EXCEPTIONS.get(code, "exception %d" % code), message or ""))


def read_bool(reader):
    check_exception(reader)
    return reader.read_int32() != 0


def read_int(reader):
    check_exception(reader)
    return reader.read_int32()


def read_string_list(reader):
    check_exception(reader)
    n = reader.read_int32()
    if n < 0:
        return None
    return [reader.read_string16() for _ in range(n)]


def self_check_ok(has_passive, providers):
    """The two read-only answers that prove the codes fit this image."""
    return has_passive is True and isinstance(providers, list) and "passive" in providers


def read_provider_request(reader):
    """onProviderRequestChanged(String provider, in ProviderRequest): the
    provider's name and whether anything asks it for locations now (and
    how often, ms). The interface token is already read (libgbinder)."""
    provider = reader.read_string16()
    if reader.read_int32() == 0:  # a null request: nothing asks
        return provider, False, None
    interval = reader.read_int64()
    if interval == INTERVAL_DISABLED or interval < 0:
        return provider, False, None
    return provider, True, interval
