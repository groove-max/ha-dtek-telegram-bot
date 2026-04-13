"""Feature registry."""

from features.emergency import EmergencyFeature
from features.group_change import GroupChangeFeature
from features.power_presence import PowerPresenceFeature
from features.schedule_change import ScheduleChangeFeature
from features.status_message import StatusMessageFeature
from features.upcoming_outage import UpcomingOutageFeature
from features.voltage import VoltageFeature

ALL_FEATURES = [
    ScheduleChangeFeature,
    EmergencyFeature,
    GroupChangeFeature,
    VoltageFeature,
    PowerPresenceFeature,
    UpcomingOutageFeature,
    StatusMessageFeature,
]
