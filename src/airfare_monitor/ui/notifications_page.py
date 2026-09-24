"""Desktop alerts and opt-in personal SMTP configuration."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, QSize, QThread, Signal, Qt
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QGridLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout,
    QWidget,
)

from ..desktop_app.credential_store import CredentialStore, CredentialStoreError
from ..desktop_app.mail_errors import friendly_mail_error
from ..desktop_app.mail_profile import MailProfile, MailProfileRepository
from ..desktop_app.preferences import PreferencesManager
from ..mail import SmtpCredentials, send_test_message
from .route_wizard import StepperSpinBox


class ToggleSwitch(QCheckBox):
    """Compact accessible switch used by notification settings."""

    def __init__(self, accessible_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAccessibleName(accessible_name)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(62, 36)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(62, 36)

    def hitButton(self, position: QPoint) -> bool:  # noqa: N802 - Qt API
        """Treat the complete painted switch as clickable.

        QCheckBox otherwise derives a much smaller hit rectangle from the
        platform's native checkbox indicator.  That rectangle does not follow
        our custom-drawn knob, which made the switch effectively unclickable
        after it moved to the off position.
        """

        return self.rect().contains(position)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 4, 60, 28)
        if self.isChecked():
            gradient = QLinearGradient(track.topLeft(), track.topRight())
            gradient.setColorAt(0, QColor("#1d72ed"))
            gradient.setColorAt(1, QColor("#0c61db"))
            painter.setBrush(gradient)
        else:
            painter.setBrush(QColor("#cbd6e4"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(track, 14, 14)
        knob_x = 47 if self.isChecked() else 15
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QPointF(knob_x, 18), 11, 11)


class NotificationIcon(QWidget):
    """Small code-drawn icon badge so the packaged UI needs no extra assets."""

    def __init__(self, kind: str, tone: str = "blue", parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind
        self.tone = tone
        self.setFixedSize(58, 58)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(2, 2, 54, 54)
        gradient = QLinearGradient(area.topLeft(), area.bottomRight())
        if self.tone == "violet":
            gradient.setColorAt(0, QColor("#f1eaff"))
            gradient.setColorAt(1, QColor("#d9c9ff"))
            stroke = QColor("#7c4ce8")
        else:
            gradient.setColorAt(0, QColor("#e9f3ff"))
            gradient.setColorAt(1, QColor("#c8ddff"))
            stroke = QColor("#176de8")
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(area, 15, 15)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(stroke, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if self.kind == "desktop":
            painter.drawRoundedRect(QRectF(15, 15, 28, 20), 3, 3)
            painter.drawLine(QPointF(29, 35), QPointF(29, 42))
            painter.drawLine(QPointF(23, 42), QPointF(35, 42))
        elif self.kind == "mail":
            painter.drawRoundedRect(QRectF(13, 17, 32, 24), 4, 4)
            painter.drawLine(QPointF(14, 19), QPointF(29, 31))
            painter.drawLine(QPointF(44, 19), QPointF(29, 31))
        else:
            path = QPainterPath(QPointF(29, 13))
            path.lineTo(42, 19)
            path.lineTo(40, 34)
            path.quadTo(38, 42, 29, 46)
            path.quadTo(20, 42, 18, 34)
            path.lineTo(16, 19)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(29, 20), QPointF(29, 38))


class _MailTestWorker(QObject):
    finished = Signal(bool, str)

    def __init__(self, settings, credentials: SmtpCredentials):
        super().__init__()
        self.settings = settings
        self.credentials = credentials

    def run(self) -> None:
        try:
            send_test_message(self.settings, self.credentials)
        except Exception as exc:
            self.finished.emit(False, friendly_mail_error(exc))
        else:
            self.finished.emit(True, "服务器连接、身份验证和投递请求均已完成。")


class NotificationsPage(QWidget):
    settings_saved = Signal()

    def __init__(
        self,
        preferences: PreferencesManager,
        repository: MailProfileRepository,
        credential_store: CredentialStore | None = None,
    ):
        super().__init__()
        self.setObjectName("pageCanvas")
        self.preferences = preferences
        self.repository = repository
        self.credential_store = credential_store or CredentialStore()
        self._thread: QThread | None = None
        self._worker: _MailTestWorker | None = None
        self._pending_profile: MailProfile | None = None
        self._pending_secret: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(30, 27, 30, 22)
        root.setSpacing(7)
        root.addWidget(QLabel("通知设置", objectName="pageTitle"))
        root.addWidget(QLabel("只在重要变化时提醒您；桌面和邮件可独立启用。", objectName="muted"))
        root.addSpacing(7)
        scroll = QScrollArea(objectName="notificationScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget(objectName="pageHost")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(8, 2, 12, 12)
        layout.setSpacing(16)

        desktop_card = _card()
        desktop_header = QHBoxLayout()
        desktop_header.setSpacing(15)
        desktop_header.addWidget(NotificationIcon("desktop"))
        desktop_copy = QVBoxLayout()
        desktop_copy.setSpacing(3)
        desktop_copy.addWidget(QLabel("桌面通知", objectName="notificationSectionTitle"))
        desktop_copy.addWidget(QLabel("开启后，当监控到重要事件时在桌面弹出通知。", objectName="notificationSubtitle"))
        desktop_header.addLayout(desktop_copy, 1)
        self.desktop_toggle = ToggleSwitch("桌面通知")
        self.desktop_toggle.setChecked(preferences.load().desktop_notifications)
        self.desktop_state = QLabel(objectName="switchState")
        desktop_header.addWidget(self.desktop_toggle)
        desktop_header.addWidget(self.desktop_state)
        desktop_card.layout().addLayout(desktop_header)
        desktop_card.layout().addWidget(_divider())
        desktop_card.layout().addWidget(QLabel("触发事件", objectName="notificationFieldTitle"))
        events = QHBoxLayout()
        events.setSpacing(12)
        for event_name in ("低价命中", "查询失败", "需要人工处理", "邮件失败"):
            events.addWidget(QLabel(f"✓  {event_name}", objectName="eventChip"))
        events.addStretch()
        desktop_card.layout().addLayout(events)
        desktop_card.layout().addWidget(QLabel(
            "提醒确认低价、部分/全部失败、需要人工处理及邮件发送失败。普通成功轮次不打扰；应用内最近动态始终保留。",
            objectName="notificationHelp", wordWrap=True,
        ))
        layout.addWidget(desktop_card)

        mail_card = _card()
        mail_header = QHBoxLayout()
        mail_header.setSpacing(15)
        mail_header.addWidget(NotificationIcon("mail", "violet"))
        mail_copy = QVBoxLayout()
        mail_copy.setSpacing(3)
        mail_copy.addWidget(QLabel("我的邮件通知", objectName="notificationSectionTitle"))
        mail_copy.addWidget(QLabel("配置 SMTP 发送邮件通知，授权码不会写入配置文件。", objectName="notificationSubtitle"))
        mail_header.addLayout(mail_copy, 1)
        self.mail_toggle = ToggleSwitch("邮件通知")
        self.mail_state = QLabel(objectName="switchState")
        mail_header.addWidget(self.mail_toggle)
        mail_header.addWidget(self.mail_state)
        mail_card.layout().addLayout(mail_header)
        security_note = QFrame(objectName="credentialCard")
        security_layout = QHBoxLayout(security_note)
        security_layout.setContentsMargins(14, 10, 15, 10)
        security_layout.setSpacing(12)
        security_layout.addWidget(NotificationIcon("shield"))
        security_copy = QVBoxLayout()
        security_copy.setSpacing(2)
        security_copy.addWidget(QLabel("授权码保存在系统安全凭据中（Windows 凭据管理器 / macOS 钥匙串）", objectName="credentialTitle"))
        security_copy.addWidget(QLabel("航价守望不会把授权码写入 YAML、日志、诊断文件或安装包。", objectName="notificationSubtitle", wordWrap=True))
        security_layout.addLayout(security_copy, 1)
        mail_card.layout().addWidget(security_note)
        self.host = QLineEdit()
        self.host.setPlaceholderText("例如 smtp.example.com")
        self.port = StepperSpinBox()
        self.port.setRange(1, 65535)
        self.security = QComboBox()
        self.security.addItem("SSL", "ssl")
        self.security.addItem("STARTTLS", "starttls")
        self.username = QLineEdit()
        self.username.setPlaceholderText("邮箱账号")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("授权码；留空则使用已保存的授权码")
        self.password_action = self.password.addAction(_eye_icon(), QLineEdit.ActionPosition.TrailingPosition)
        self.password_action.setToolTip("显示或隐藏授权码")
        self.password_action.triggered.connect(self._toggle_password_visibility)
        self.sender = QLineEdit()
        self.sender.setPlaceholderText("发件邮箱")
        self.recipients = QLineEdit()
        self.recipients.setPlaceholderText("多个收件邮箱用逗号分隔")
        self.attach = QCheckBox("邮件附带 Excel 报告", objectName="reportAttachment")
        form = QGridLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        for index, (label, widget) in enumerate((
            ("SMTP 服务器", self.host), ("端口", self.port),
            ("安全方式", self.security), ("SMTP 授权码", self.password),
            ("邮箱账号", self.username), ("发件人", self.sender),
            ("收件人", self.recipients), ("报告附件", self.attach),
        )):
            form.addLayout(_field(label, widget), index // 2, index % 2)
        mail_card.layout().addLayout(form)
        actions = QHBoxLayout()
        self.status = QLabel("尚未发送测试邮件", objectName="muted", wordWrap=True)
        actions.addWidget(self.status, 1)
        self.reset_button = QPushButton("重置")
        self.reset_button.clicked.connect(self._reset_form)
        self.save_button = QPushButton("保存设置")
        self.save_button.clicked.connect(self._save_all)
        self.test_button = QPushButton("发送测试邮件并保存", objectName="primary")
        self.test_button.clicked.connect(self._test_mail)
        actions.addWidget(self.reset_button)
        actions.addWidget(self.save_button)
        actions.addWidget(self.test_button)
        mail_card.layout().addLayout(actions)
        layout.addWidget(mail_card)
        layout.addStretch()
        scroll.setWidget(host)
        root.addWidget(scroll, 1)
        self.desktop_toggle.toggled.connect(self._refresh_switch_labels)
        self.mail_toggle.toggled.connect(self._refresh_switch_labels)
        self.load()

    def load(self) -> None:
        profile = self.repository.load()
        self.mail_toggle.setChecked(profile.enabled)
        self.host.setText(profile.smtp_host)
        self.port.setValue(profile.smtp_port)
        self.security.setCurrentIndex(max(0, self.security.findData(profile.security)))
        self.username.setText(profile.username)
        self.sender.setText(profile.sender)
        self.recipients.setText(", ".join(profile.recipients))
        self.attach.setChecked(profile.attach_excel)
        self.password.clear()
        self.desktop_toggle.setChecked(self.preferences.load().desktop_notifications)
        self._refresh_switch_labels()

    def _refresh_switch_labels(self) -> None:
        self.desktop_state.setText("已开启" if self.desktop_toggle.isChecked() else "已关闭")
        self.mail_state.setText("已开启" if self.mail_toggle.isChecked() else "已关闭")

    def _toggle_password_visibility(self) -> None:
        hidden = self.password.echoMode() == QLineEdit.EchoMode.Password
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)

    def _reset_form(self) -> None:
        self.load()
        self.status.setText("已恢复为上次保存的设置")

    def _profile(self, *, require_delivery: bool = False) -> MailProfile:
        profile = MailProfile(
            enabled=self.mail_toggle.isChecked(),
            smtp_host=self.host.text().strip(),
            smtp_port=self.port.value(),
            security=str(self.security.currentData()),
            username=self.username.text().strip(),
            sender=self.sender.text().strip(),
            recipients=tuple(item.strip() for item in self.recipients.text().split(",") if item.strip()),
            attach_excel=self.attach.isChecked(),
        )
        profile.validate(require_delivery=require_delivery)
        return profile

    def _save_desktop(self, *, update_status: bool = True) -> bool:
        try:
            current = self.preferences.load()
            self.preferences.repository.save_desktop(
                replace(current, desktop_notifications=self.desktop_toggle.isChecked())
            )
        except Exception:
            QMessageBox.warning(self, "设置未保存", "桌面通知设置保存失败，请稍后重试。")
            return False
        self.settings_saved.emit()
        if update_status:
            self.status.setText("桌面通知设置已保存")
        return True

    def _save_all(self) -> None:
        if not self._save_desktop(update_status=False):
            return
        self._save_mail()

    def _save_mail(self) -> None:
        try:
            profile = self._profile()
            self._commit(profile, self.password.text() or None)
        except (ValueError, CredentialStoreError, OSError) as exc:
            QMessageBox.warning(self, "邮件设置未保存", str(exc))
            return
        self.status.setText("邮件设置已保存；可点击测试邮件确认通路")

    def _commit(self, profile: MailProfile, secret: str | None) -> None:
        previous = self.repository.load()
        old_secret = None
        if secret:
            old_secret = self.credential_store.get_secret(profile.username)
            self.credential_store.save_secret(profile.username, secret)
        elif profile.enabled and not self.credential_store.has_secret(profile.username):
            raise CredentialStoreError("请填写 SMTP 授权码并保存")
        try:
            self.repository.save(profile)
        except Exception:
            if secret:
                if old_secret:
                    self.credential_store.save_secret(profile.username, old_secret)
                else:
                    self.credential_store.delete_secret(profile.username)
            raise
        if previous.username and previous.username != profile.username:
            self.credential_store.delete_secret(previous.username)
        self.password.clear()
        self.settings_saved.emit()

    def _test_mail(self) -> None:
        if self._thread is not None:
            return
        try:
            profile = self._profile(require_delivery=True)
            secret = self.password.text() or self.credential_store.get_secret(profile.username)
            credentials = profile.credentials(secret or "")
            settings = profile.mail_settings(self.preferences.repository.load_core().mail)
        except (ValueError, CredentialStoreError) as exc:
            QMessageBox.warning(self, "无法测试邮件", str(exc))
            return
        self._pending_profile = profile
        self._pending_secret = self.password.text() or None
        self.test_button.setEnabled(False)
        self.save_button.setEnabled(False)
        self.reset_button.setEnabled(False)
        self.status.setText("正在连接邮件服务器；窗口仍可正常使用…")
        thread = QThread(self)
        worker = _MailTestWorker(settings, credentials)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._test_finished)
        worker.finished.connect(thread.quit, Qt.ConnectionType.DirectConnection)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread = thread
        self._worker = worker
        thread.start()

    def _test_finished(self, success: bool, message: str) -> None:
        self.status.setText(message)
        if success and self._pending_profile is not None:
            try:
                self._commit(self._pending_profile, self._pending_secret)
            except (ValueError, CredentialStoreError, OSError):
                self.status.setText("测试邮件已发送，但设置保存失败；请重新保存。")
            else:
                self.status.setText("测试成功，设置已保存：服务器连接、认证和投递请求均已完成。")
        self._pending_profile = None
        self._pending_secret = None
        self.test_button.setEnabled(True)
        self.save_button.setEnabled(True)
        self.reset_button.setEnabled(True)

    def _thread_finished(self) -> None:
        self._worker = None
        self._thread = None

    def finish_pending_test(self) -> bool:
        """Keep a mail worker alive until it exits during normal application quit."""

        if self._thread is not None:
            return self._thread.wait(35000)
        return True


def _card() -> QFrame:
    frame = QFrame(objectName="notificationCard")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 17, 20, 17)
    layout.setSpacing(11)
    return frame


def _field(label: str, widget: QWidget) -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(4)
    layout.addWidget(QLabel(label, objectName="notificationFieldLabel"))
    layout.addWidget(widget)
    return layout


def _divider() -> QFrame:
    divider = QFrame(objectName="notificationDivider")
    divider.setFrameShape(QFrame.Shape.HLine)
    return divider


def _eye_icon() -> QIcon:
    pixmap = QPixmap(22, 22)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#59779d"), 1.8))
    path = QPainterPath(QPointF(2, 11))
    path.quadTo(6, 5, 11, 5)
    path.quadTo(16, 5, 20, 11)
    path.quadTo(16, 17, 11, 17)
    path.quadTo(6, 17, 2, 11)
    painter.drawPath(path)
    painter.setBrush(QColor("#59779d"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawEllipse(QPointF(11, 11), 2.7, 2.7)
    painter.end()
    return QIcon(pixmap)
