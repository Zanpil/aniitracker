"""Lucky / Super Lucky pity rules for Aniimo, and the counters rebuilt from the event log.

The rates are not in the client files (luckyresult is ServerOnly). They come from the community
transcription of the in-game "Probability Details" page (AniimoSync, 2026-10-14):
- one check = one Aniimo actually caught; Super Lucky is rolled first, Lucky only if it fails
- rate and guarantee depend on how many strikes of that type you already got this week
- weekly counts reset Monday 04:00 server time, and so do the pity counters (confirmed in game)
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# (applies while strikes already had this week < bound, chance per check, guaranteed at that check)
LUCKY_TIERS = [(10, 0.01, 200), (None, 0.005, 400)]
SUPER_TIERS = [(1, 0.002, 500), (2, 0.001, 1000), (None, 0.0005, 3000)]
SERVER_UTC_OFFSET = {"EU": 0, "NA": -4, "ASIA": 8}
RESET_HOUR = 4
SET_FIELDS = ("catches_week", "lucky_pity", "lucky_week", "super_pity", "super_week")


def tier(tiers, done):
    """(chance, guarantee) of the next strike when `done` strikes were already had this week."""
    for bound, p, cap in tiers:
        if bound is None or done < bound:
            return p, cap


def week_start(ts, server="EU"):
    """Unix time of the last Monday 04:00 server time at or before ts."""
    tz = timezone(timedelta(hours=SERVER_UTC_OFFSET[server]))
    t = datetime.fromtimestamp(ts, tz) - timedelta(hours=RESET_HOUR)
    monday = t.date() - timedelta(days=t.weekday())
    return datetime(monday.year, monday.month, monday.day, RESET_HOUR, tzinfo=tz).timestamp()


@dataclass
class State:
    lucky_pity: int = 0   # checks since the last Lucky
    lucky_week: int = 0
    super_pity: int = 0   # checks since the last Super Lucky
    super_week: int = 0
    catches_week: int = 0
    catches_total: int = 0
    lucky_total: int = 0
    super_total: int = 0
    week: float | None = None


def build(events, now, server="EU", reset_pity_weekly=True):
    """Replay events ({"ts", "k": catch|lucky|super|set, ...}) into a State as of `now`."""
    s = State()

    def roll(ts):
        w = week_start(ts, server)
        if w != s.week:
            s.lucky_week = s.super_week = s.catches_week = 0
            if reset_pity_weekly:
                s.lucky_pity = s.super_pity = 0
            s.week = w

    for e in events:
        roll(e["ts"])
        k = e["k"]
        if k == "set":
            for f in SET_FIELDS:
                if f in e:
                    setattr(s, f, e[f])
            continue
        s.catches_week += 1
        s.catches_total += 1
        if k == "super":
            # Lucky is not rolled on the catch that gave the Super Lucky
            s.super_pity, s.super_week, s.super_total = 0, s.super_week + 1, s.super_total + 1
            continue
        s.super_pity += 1
        if k == "lucky":
            s.lucky_pity, s.lucky_week, s.lucky_total = 0, s.lucky_week + 1, s.lucky_total + 1
        else:
            s.lucky_pity += 1
    roll(now)
    return s


@dataclass
class Odds:
    pity: int
    done: int
    p: float
    cap: int

    @property
    def left(self):
        """Checks until the guaranteed one, inclusive."""
        return max(self.cap - self.pity, 0)

    @property
    def had_it(self):
        """Share of players who would already have had one after this many checks."""
        return 1 - (1 - self.p) ** min(self.pity, self.cap)

    @property
    def expected(self):
        """Average checks still needed, guarantee included."""
        n = self.left
        return (1 - (1 - self.p) ** n) / self.p if n else 0


def odds(s):
    return (Odds(s.lucky_pity, s.lucky_week, *tier(LUCKY_TIERS, s.lucky_week)),
            Odds(s.super_pity, s.super_week, *tier(SUPER_TIERS, s.super_week)))
