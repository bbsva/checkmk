#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2019 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import time
import pytest
from pathlib import Path

from testlib import on_time, is_managed_repo

import cmk.utils.paths
from cmk.utils.type_defs import UserId
import cmk.utils.version

from cmk.gui.exceptions import MKUserError, MKAuthException
from cmk.gui.valuespec import Dictionary
import cmk.gui.config as config
import cmk.gui.userdb as userdb
from cmk.gui.globals import g
import cmk.gui.plugins.userdb.utils as utils
import cmk.gui.plugins.userdb.htpasswd as htpasswd
import cmk.gui.plugins.userdb.ldap_connector as ldap


@pytest.fixture(name="fix_time", autouse=True)
def fixture_time():
    with on_time("2019-09-05 00:00:00", "UTC"):
        yield


@pytest.fixture(name="user_id")
def fixture_user_id(with_user):
    return UserId(with_user[0])


# user_id needs to be used here because it executes a reload of the config and the monkeypatch of
# the config needs to be done after loading the config
@pytest.fixture()
def single_user_session_enabled(monkeypatch, user_id):
    monkeypatch.setattr(config, "single_user_session", 10)
    assert config.single_user_session == 10


# user_id needs to be used here because it executes a reload of the config and the monkeypatch of
# the config needs to be done after loading the config
@pytest.fixture()
def save_user_access_times_enabled(monkeypatch, user_id):
    monkeypatch.setattr(config, "save_user_access_times", True)
    assert config.save_user_access_times is True


# user_id needs to be used here because it executes a reload of the config and the monkeypatch of
# the config needs to be done after loading the config
@pytest.fixture()
def lock_on_logon_failures_enabled(monkeypatch, user_id):
    monkeypatch.setattr(config, "lock_on_logon_failures", 3)
    assert config.lock_on_logon_failures == 3


# user_id needs to be used here because it executes a reload of the config and the monkeypatch of
# the config needs to be done after loading the config
@pytest.fixture()
def user_idle_timeout_enabled(monkeypatch, user_id):
    monkeypatch.setattr(config, "user_idle_timeout", 8)
    assert config.user_idle_timeout == 8


@pytest.fixture(name="session_timed_out")
def fixture_session_timed_out(monkeypatch, user_id, fix_time):
    session_id = "sess1"
    userdb.save_custom_attr(user_id, "session_info", "%s|%s" % (session_id, int(time.time() - 20)))
    return session_id


@pytest.fixture(name="session_valid")
def fixture_session_valid(monkeypatch, user_id, fix_time):
    session_id = "sess2"
    userdb.save_custom_attr(user_id, "session_info", "%s|%s" % (session_id, int(time.time() - 5)))
    return session_id


@pytest.mark.usefixtures("single_user_session_enabled", "save_user_access_times_enabled")
def test_on_succeeded_login(user_id):
    assert config.single_user_session == 10
    assert config.save_user_access_times is True

    # Never logged in before
    assert userdb.load_session_info(user_id) is None
    assert userdb.get_user_access_time(user_id) is None
    assert userdb._load_failed_logins(user_id) == 0

    session_id = userdb.on_succeeded_login(user_id)
    assert session_id != ""

    # Verify the session was initialized
    session_info = userdb.load_session_info(user_id)
    assert session_info is not None
    assert session_info == (session_id, time.time())

    # Ensure the failed login count is 0
    assert userdb._load_failed_logins(user_id) == 0

    # Was the user access time updated?
    assert userdb.get_user_access_time(user_id) == time.time()


@pytest.mark.usefixtures("single_user_session_enabled")
def test_on_succeeded_login_without_user_access_time_enabled(user_id):
    userdb.on_succeeded_login(user_id)
    assert userdb.get_user_access_time(user_id) is None


@pytest.mark.usefixtures("single_user_session_enabled", "register_builtin_html")
def test_on_failed_login_no_locking(user_id):
    assert config.lock_on_logon_failures is False
    assert userdb._load_failed_logins(user_id) == 0
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 1
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 2
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 3
    assert userdb._user_locked(user_id) is False


@pytest.mark.usefixtures("single_user_session_enabled", "register_builtin_html")
def test_on_failed_login_count_reset_on_succeeded_login(user_id):
    assert config.lock_on_logon_failures is False
    assert userdb._load_failed_logins(user_id) == 0
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 1
    assert userdb._user_locked(user_id) is False

    userdb.on_succeeded_login(user_id)
    assert userdb._load_failed_logins(user_id) == 0
    assert userdb._user_locked(user_id) is False


@pytest.mark.usefixtures("single_user_session_enabled", "lock_on_logon_failures_enabled",
                         "register_builtin_html")
def test_on_failed_login_with_locking(user_id):
    assert config.lock_on_logon_failures == 3
    assert userdb._load_failed_logins(user_id) == 0
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 1
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 2
    assert userdb._user_locked(user_id) is False

    userdb.on_failed_login(user_id)
    assert userdb._load_failed_logins(user_id) == 3
    assert userdb._user_locked(user_id) is True


def test_on_logout_no_single_user_session(user_id):
    assert userdb.load_session_info(user_id) is None
    userdb.on_logout(user_id)
    assert userdb.load_session_info(user_id) is None


@pytest.mark.usefixtures("single_user_session_enabled")
def test_on_logout_invalidate_session(user_id):
    assert userdb.on_succeeded_login(user_id)
    assert userdb.load_session_info(user_id) is not None

    userdb.on_logout(user_id)
    assert userdb.load_session_info(user_id) is None


@pytest.mark.usefixtures("single_user_session_enabled", "save_user_access_times_enabled")
def test_on_access_update_valid_session(user_id, session_valid):
    old_session = userdb.load_session_info(user_id)
    assert old_session is not None
    old_access_time = userdb.get_user_access_time(user_id)
    assert old_access_time is None

    userdb.on_access(user_id, 10.0, session_valid)

    new_session = userdb.load_session_info(user_id)
    assert new_session is not None
    assert new_session[0] == old_session[0]
    assert new_session[1] == time.time()
    assert new_session[1] > old_session[1]

    assert userdb.get_user_access_time(user_id) == time.time()


@pytest.mark.usefixtures("single_user_session_enabled")
def test_on_access_update_idle_session(user_id, session_timed_out):
    old_session = userdb.load_session_info(user_id)
    assert old_session is not None

    userdb.on_access(user_id, 10.0, session_timed_out)

    new_session = userdb.load_session_info(user_id)
    assert new_session is not None
    assert new_session[0] == old_session[0]
    assert new_session[1] == time.time()
    assert new_session[1] > old_session[1]


@pytest.mark.usefixtures("single_user_session_enabled")
def test_on_access_update_unknown_session(user_id, session_valid):
    with pytest.raises(MKAuthException, match="Invalid user session"):
        userdb.on_access(user_id, 10.0, "xyz")


@pytest.mark.usefixtures("user_idle_timeout_enabled")
def test_on_access_logout_on_idle_timeout(user_id, session_timed_out):
    with pytest.raises(MKAuthException, match="login timed out"):
        userdb.on_access(user_id, time.time() - 10, session_timed_out)


@pytest.mark.usefixtures("single_user_session_enabled")
def test_on_succeeded_login_already_existing_session(user_id, session_valid):
    with pytest.raises(MKUserError, match="Another session"):
        assert userdb.on_succeeded_login(user_id)


def test_is_valid_user_session_single_user_session_disabled(user_id):
    assert config.single_user_session is None
    assert userdb._is_valid_user_session(user_id, "session1") is True


@pytest.mark.usefixtures("single_user_session_enabled")
def test_is_valid_user_session_not_existing(user_id):
    assert userdb._is_valid_user_session(user_id, "not-existing-session") is False


@pytest.mark.usefixtures("single_user_session_enabled")
def test_is_valid_user_session_still_valid_when_last_activity_extends_timeout(
        user_id, session_timed_out):
    assert userdb._is_valid_user_session(user_id, session_timed_out) is True


@pytest.mark.usefixtures("single_user_session_enabled")
def test_is_valid_user_session_valid(user_id, session_valid):
    assert userdb._is_valid_user_session(user_id, session_valid) is True


def test_ensure_user_can_init_no_single_user_session(user_id):
    assert config.single_user_session is None
    assert userdb._ensure_user_can_init_session(user_id) is True


@pytest.mark.usefixtures("single_user_session_enabled")
def test_ensure_user_can_init_no_previous_session(user_id):
    assert userdb._ensure_user_can_init_session(user_id) is True


@pytest.mark.usefixtures("single_user_session_enabled")
def test_ensure_user_can_init_with_previous_session_timeout(monkeypatch, user_id):
    assert userdb._ensure_user_can_init_session(user_id) is True


@pytest.mark.usefixtures("single_user_session_enabled")
def test_ensure_user_can_not_init_with_previous_session(user_id, session_valid):
    with pytest.raises(MKUserError, match="Another session"):
        assert userdb._ensure_user_can_init_session(user_id) is False


def test_initialize_session_single_user_session_not_enabled(user_id):
    assert userdb._initialize_session(user_id) == ""


@pytest.mark.usefixtures("single_user_session_enabled")
def test_initialize_session_single_user_session(user_id):
    session_id = userdb._initialize_session(user_id)
    assert session_id != ""
    assert userdb.load_session_info(user_id) == (session_id, int(time.time()))


@pytest.mark.usefixtures("single_user_session_enabled")
def test_create_session_id_is_correct_type():
    id1 = userdb._create_session_id()
    assert isinstance(id1, str)


@pytest.mark.usefixtures("single_user_session_enabled")
def test_create_session_id_changes():
    assert userdb._create_session_id() != userdb._create_session_id()


def test_refresh_session_single_user_session_not_enabled(user_id):
    assert config.single_user_session is None
    userdb._refresh_session(user_id)
    assert userdb.load_session_info(user_id) is None


@pytest.mark.usefixtures("single_user_session_enabled")
def test_refresh_session_success(user_id, session_valid):
    session_info = userdb.load_session_info(user_id)
    assert session_info is not None
    old_session_id, old_last_activity = session_info

    with on_time("2019-09-05 00:00:30", "UTC"):
        userdb._refresh_session(user_id)

        new_session_info = userdb.load_session_info(user_id)
        assert new_session_info is not None
        new_session_id, new_last_activity = new_session_info
        assert old_session_id == new_session_id
        assert new_last_activity > old_last_activity


def test_invalidate_session_single_user_session_disabled(user_id, session_valid):
    assert userdb.load_session_info(user_id) is not None
    userdb._invalidate_session(user_id)
    assert userdb.load_session_info(user_id) is None


@pytest.mark.usefixtures("single_user_session_enabled")
def test_invalidate_session(user_id, session_valid):
    assert userdb.load_session_info(user_id) is not None
    userdb._invalidate_session(user_id)
    assert userdb.load_session_info(user_id) is None


def test_user_attribute_sync_plugins(monkeypatch):
    monkeypatch.setattr(config, "wato_user_attrs", [{
        'add_custom_macro': False,
        'help': u'VIP attribute',
        'name': 'vip',
        'show_in_table': False,
        'title': u'VIP',
        'topic': 'ident',
        'type': 'TextAscii',
        'user_editable': True
    }])

    monkeypatch.setattr(utils, "user_attribute_registry", utils.UserAttributeRegistry())
    monkeypatch.setattr(userdb, "user_attribute_registry", utils.user_attribute_registry)
    monkeypatch.setattr(ldap, "ldap_attribute_plugin_registry", ldap.LDAPAttributePluginRegistry())

    assert "vip" not in utils.user_attribute_registry
    assert "vip" not in ldap.ldap_attribute_plugin_registry

    userdb.update_config_based_user_attributes()

    assert "vip" in utils.user_attribute_registry
    assert "vip" in ldap.ldap_attribute_plugin_registry

    connection = ldap.LDAPUserConnector({
        "id": "ldp",
        "directory_type": ("ad", {
            "connect_to": ("fixed_list", {
                "server": "127.0.0.1",
            })
        })
    })

    ldap_plugin = ldap.ldap_attribute_plugin_registry["vip"]()
    assert ldap_plugin.title == "VIP"
    assert ldap_plugin.help == "VIP attribute"
    assert ldap_plugin.needed_attributes(connection, {"attr": "vip_attr"}) == ["vip_attr"]
    assert ldap_plugin.needed_attributes(connection, {"attr": "vip_attr"}) == ["vip_attr"]
    assert isinstance(ldap_plugin.parameters(connection), Dictionary)

    # Test removing previously registered ones
    monkeypatch.setattr(config, "wato_user_attrs", [])
    userdb.update_config_based_user_attributes()

    assert "vip" not in utils.user_attribute_registry
    assert "vip" not in ldap.ldap_attribute_plugin_registry


def test_check_credentials_local_user(with_user):
    username, password = with_user
    assert userdb.check_credentials(username, password) == username


@pytest.mark.usefixtures("register_builtin_html")
def test_check_credentials_local_user_create_htpasswd_user_ad_hoc():
    user_id = UserId("sha256user")
    assert userdb.user_exists(user_id) is False
    assert userdb._user_exists_according_to_profile(user_id) is False
    assert user_id not in userdb.load_users(lock=False)
    # TODO: It's bad that we have to do this here (after each load_users)
    del g.users

    htpasswd.Htpasswd(Path(cmk.utils.paths.htpasswd_file)).save(
        {"sha256user": htpasswd.hash_password("cmk")})
    # Once a user exists in the htpasswd, the GUI treats the user as existing user and will
    # automatically initialize the missing data structures
    assert userdb.user_exists(user_id) is True
    assert userdb._user_exists_according_to_profile(user_id) is False
    assert str(user_id) in userdb.load_users(lock=True)
    # TODO: It's bad that we have to do this here (after each load_users)
    del g.users

    assert userdb.check_credentials(user_id, "cmk") == user_id

    # Nothing changes during regular access
    assert userdb.user_exists(user_id) is True
    assert userdb._user_exists_according_to_profile(user_id) is False
    assert str(user_id) in userdb.load_users(lock=False)


def test_check_credentials_local_user_disallow_locked(with_user):
    user_id, password = with_user
    assert userdb.check_credentials(user_id, password) == user_id

    users = userdb.load_users(lock=True)
    users[user_id]["locked"] = True
    userdb.save_users(users)

    assert userdb.check_credentials(user_id, password) is False


# user_id needs to be used here because it executes a reload of the config and the monkeypatch of
# the config needs to be done after loading the config
@pytest.fixture()
def make_cme(monkeypatch, user_id):
    if not is_managed_repo():
        pytest.skip("not relevant")

    monkeypatch.setattr(cmk.utils.version, "omd_version", lambda: "2.0.0i1.cme")
    assert cmk.utils.version.is_managed_edition()

    monkeypatch.setattr(config, "current_customer", "test-customer")
    # Fix CRE mypy tests that do not have this attribute defined
    assert config.current_customer == "test-customer"  # type: ignore[attr-defined]


@pytest.fixture()
def make_cme_global_user(user_id):
    if not is_managed_repo():
        pytest.skip("not relevant")

    import cmk.gui.cme.managed as managed  # pylint: disable=no-name-in-module
    users = userdb.load_users(lock=True)
    users[user_id]["customer"] = managed.SCOPE_GLOBAL
    userdb.save_users(users)


@pytest.fixture()
def make_cme_customer_user(user_id):
    if not is_managed_repo():
        pytest.skip("not relevant")

    users = userdb.load_users(lock=True)
    users[user_id]["customer"] = "test-customer"
    userdb.save_users(users)


@pytest.fixture()
def make_cme_wrong_customer_user(user_id):
    if not is_managed_repo():
        pytest.skip("not relevant")

    users = userdb.load_users(lock=True)
    users[user_id]["customer"] = "wrong-customer"
    userdb.save_users(users)


@pytest.mark.usefixtures("make_cme", "make_cme_global_user")
def test_check_credentials_managed_global_user_is_allowed(with_user):
    if not is_managed_repo():
        pytest.skip("not relevant")

    user_id, password = with_user
    assert userdb.check_credentials(user_id, password) == user_id


@pytest.mark.usefixtures("make_cme", "make_cme_customer_user")
def test_check_credentials_managed_customer_user_is_allowed(with_user):
    if not is_managed_repo():
        pytest.skip("not relevant")

    user_id, password = with_user
    assert userdb.check_credentials(user_id, password) == user_id


@pytest.mark.usefixtures("make_cme", "make_cme_wrong_customer_user")
def test_check_credentials_managed_wrong_customer_user_is_denied(with_user):
    if not is_managed_repo():
        pytest.skip("not relevant")

    user_id, password = with_user
    assert userdb.check_credentials(user_id, password) is False
