"""Admin permissions, settings, statistics, blocking and audit (TZ §11, §15)."""
from __future__ import annotations

import admin
import services
from tests.helpers import FakeBot, make_contest, make_user


async def test_owner_ids_are_admins_and_owners(session):
    assert await admin.is_admin(session, 1000) is True   # from OWNER_IDS env
    assert await admin.is_owner(session, 1000) is True
    assert await admin.is_admin(session, 555) is False
    assert await admin.is_owner(session, 555) is False


async def test_add_and_remove_admin(session):
    await admin.add_admin(session, telegram_id=555, level=1, added_by=1000)
    assert await admin.is_admin(session, 555) is True
    assert await admin.is_owner(session, 555) is False

    await admin.add_admin(session, telegram_id=777, level=2, added_by=1000)
    assert await admin.is_owner(session, 777) is True

    assert await admin.remove_admin(session, 555) is True
    assert await admin.is_admin(session, 555) is False
    # owner ids cannot be removed
    assert await admin.remove_admin(session, 1000) is False


async def test_settings_defaults_and_override(session):
    assert await admin.get_setting(session, "project_name") == "Fable Randomizer"
    await admin.set_setting(session, "project_name", "MyProject")
    assert await admin.get_setting(session, "project_name") == "MyProject"
    values = await admin.all_settings(session)
    assert values["project_name"] == "MyProject"
    assert "referral_rate" in values


async def test_owner_only_settings_declared(session):
    assert "admin_ids" in admin.OWNER_ONLY_SETTINGS
    assert "referral_rate" in admin.OWNER_ONLY_SETTINGS


async def test_stats_counts(session):
    await make_user(session, 1, "Alice")
    await make_user(session, 2, "Bob", referrer=1)
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    await services.join_contest_by_id(session, bot, telegram_id=2, contest_id=contest.id)
    await services.finish_and_draw(session, bot, contest=contest, run_by=1000)

    stats = await admin.get_stats(session)
    assert stats.users_total == 2
    assert stats.entries_total == 2
    assert stats.winners_total == 1
    assert stats.referrals_total == 1
    assert stats.contests_total == 1
    assert stats.contests_finished == 1


async def test_block_user_keeps_history(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)

    user = await admin.find_user(session, 1)
    await admin.set_blocked(session, user, True, "spam")
    assert user.is_blocked is True
    # history is untouched
    history = await admin.user_history(session, user)
    assert len(history) == 1

    await admin.set_blocked(session, user, False)
    assert user.is_blocked is False


async def test_audit_log_records_actions(session):
    await admin.audit(
        session, actor_id=1000, action="contest_create", target_type="contest", target_id=5
    )
    logs = await admin.recent_audit(session)
    assert logs[0].action == "contest_create"
    assert logs[0].target_id == "5"


async def test_contest_report_serialisable(session):
    await make_user(session, 1, "Alice")
    contest = await make_contest(session, winners=1, channels=[])
    bot = FakeBot({})
    await services.join_contest_by_id(session, bot, telegram_id=1, contest_id=contest.id)
    await services.finish_and_draw(session, bot, contest=contest, run_by=1000)
    report = await admin.contest_report(session, contest.id)
    assert report["contest"]["id"] == contest.id
    assert len(report["participants"]) == 1
    assert len(report["draws"]) == 1
    assert report["draws"][0]["commitment"]
