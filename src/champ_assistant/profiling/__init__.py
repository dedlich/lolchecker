"""Pre-game enemy profiling via the Riot Web API.

Reads only public data the user could see on op.gg / u.gg manually:
match history, champion mastery, current win/loss streak. Requires a
user-supplied Riot API key — we never embed one because Riot revokes
shared keys and the dev key is rate-limited per developer account.
"""
from .profile import EnemyProfile, ProfileService
from .riot_api import RiotApiClient, RiotApiError

__all__ = [
    "EnemyProfile",
    "ProfileService",
    "RiotApiClient",
    "RiotApiError",
]
