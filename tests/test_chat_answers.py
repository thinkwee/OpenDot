"""A short "ok" while an agent waits is the tap; setup mail and our own sent mail stay quiet."""

from opendot.ext import email as em
from opendot.runtime import _yes_no


def test_yes_no():
    for t in ["可以", "好的！", "ok", "Yes", "发吧", "go ahead 👍"]:
        assert _yes_no(t) is True, t
    for t in ["不要", "算了", "not now", "No."]:
        assert _yes_no(t) is False, t
    for t in ["可以，但是改一下标题", "ok send it to Bob instead", "给他发邮件"]:
        assert _yes_no(t) is None, t


def test_setup_mail_is_quiet():
    assert em._quiet_mail("x@gmail.com", em.TEST_SUBJECT)
    assert em._quiet_mail("no-reply@accounts.google.com", "Security alert")
    assert not em._quiet_mail("host@example.com", "Your booking")
