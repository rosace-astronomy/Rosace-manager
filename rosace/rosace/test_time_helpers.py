"""
Tests des helpers date/heure partagés (time_helpers.py).
Lancement : pytest test_time_helpers.py -v
"""

from datetime import datetime, timedelta, timezone

from rosace.time_helpers import astronomical_day_local_date, astronomical_day_start_utc


class TestAstronomicalDayStartUtc:

    def test_spans_local_noon(self):
        """La frontière est le midi local précédent — robuste au fuseau de la machine de test."""
        system_tz = datetime.now().astimezone().tzinfo
        local_date = datetime.now(system_tz).date()
        just_after_noon = datetime(local_date.year, local_date.month, local_date.day, 12, 30, tzinfo=system_tz)
        just_before_noon = just_after_noon.replace(hour=11, minute=30)

        boundary_after  = datetime.fromisoformat(astronomical_day_start_utc(just_after_noon))
        boundary_before = datetime.fromisoformat(astronomical_day_start_utc(just_before_noon))

        # crossing noon shifts the boundary by exactly one calendar day
        assert boundary_after - boundary_before == timedelta(days=1)
        # the boundary is always the most recent noon at or before `now`
        assert boundary_after <= just_after_noon.astimezone(timezone.utc)
        assert boundary_before <= just_before_noon.astimezone(timezone.utc)


class TestAstronomicalDayLocalDate:

    def test_before_local_noon_uses_previous_calendar_date(self):
        """Régression du bug de répertoire de session : juste avant midi local,
        on est encore dans la nuit d'hier."""
        system_tz = datetime.now().astimezone().tzinfo
        local_date = datetime.now(system_tz).date()
        just_before_noon = datetime(local_date.year, local_date.month, local_date.day, 11, 30, tzinfo=system_tz)

        result = astronomical_day_local_date(just_before_noon)

        assert result == (local_date - timedelta(days=1)).strftime("%Y-%m-%d")

    def test_after_local_noon_uses_current_calendar_date(self):
        system_tz = datetime.now().astimezone().tzinfo
        local_date = datetime.now(system_tz).date()
        just_after_noon = datetime(local_date.year, local_date.month, local_date.day, 12, 30, tzinfo=system_tz)

        result = astronomical_day_local_date(just_after_noon)

        assert result == local_date.strftime("%Y-%m-%d")

    def test_shares_the_same_boundary_as_start_utc(self):
        """Les deux fonctions doivent basculer exactement au même instant —
        c'est ce qui garantit que le répertoire de session et le filtre DB
        parlent de la même nuit."""
        system_tz = datetime.now().astimezone().tzinfo
        local_date = datetime.now(system_tz).date()
        moment = datetime(local_date.year, local_date.month, local_date.day, 12, 0, 0, tzinfo=system_tz)

        just_before = moment - timedelta(seconds=1)
        just_after = moment

        assert astronomical_day_local_date(just_before) != astronomical_day_local_date(just_after)
