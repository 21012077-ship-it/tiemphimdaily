import os
import json
import time
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, date, timedelta
import openpyxl
from playwright.sync_api import sync_playwright
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# Railway: dùng PostgreSQL nếu có, nếu không thì tạm dùng SQLite ở /tmp
# ẨN ĐI KHI MUỐN CHẠY TRÊN LAPTOP 
database_url = os.environ.get("DATABASE_URL")

if not database_url:
    raise RuntimeError("DATABASE_URL is not set")

# # Tự động lấy biến môi trường, nếu không có thì dùng file local.db (SQLite)
# database_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")


app.config["SQLALCHEMY_DATABASE_URI"] = database_url

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Vui lòng đăng nhập để tiếp tục.'
login_manager.login_message_category = 'warning'


# --- MODELS ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    full_name = db.Column(db.String(100), default='')
    role = db.Column(db.String(20), default='ctv')  # admin | ctv | tv
    active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def is_active(self):
        return self.active

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class StandardSub(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(100))
    source = db.Column(db.String(50))
    contact_link = db.Column(db.String(255), default="")
    expiry_date = db.Column(db.Date)
    payment_status = db.Column(db.String(50), default="Chưa thu")
    active = db.Column(db.Boolean, default=True)
    created_by_user_id = db.Column(db.Integer, nullable=True)
    created_by_name = db.Column(db.String(100), default='')

    @property
    def days_left(self):
        return (self.expiry_date - date.today()).days


class PremiumAccount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True)
    slots = db.relationship('PremiumSlot', backref='account', lazy=True, cascade="all, delete-orphan")

    @property
    def active_count(self):
        return sum(1 for slot in self.slots if slot.active)


class PremiumSlot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey('premium_account.id'), nullable=False)
    customer_name = db.Column(db.String(100))
    profile_name = db.Column(db.String(50))
    source = db.Column(db.String(50))
    contact_link = db.Column(db.String(255), default="")
    expiry_date = db.Column(db.Date)
    payment_status = db.Column(db.String(50), default="Chưa thu")
    active = db.Column(db.Boolean, default=True)
    created_by_user_id = db.Column(db.Integer, nullable=True)
    created_by_name = db.Column(db.String(100), default='')

    @property
    def days_left(self):
        return (self.expiry_date - date.today()).days


class AccountVault(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True)
    password = db.Column(db.String(100))
    plan = db.Column(db.String(50))
    cookies = db.Column(db.Text)
    status = db.Column(db.String(50), default="Chưa Check")
    assigned_to = db.Column(db.String(100), default="")
    assigned_to_user_id = db.Column(db.Integer, nullable=True)
    assigned_at = db.Column(db.DateTime, nullable=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    next_recheck_at = db.Column(db.DateTime, nullable=True)

    @property
    def days_until_resend(self):
        if not self.next_recheck_at:
            return None
        diff = self.next_recheck_at - datetime.now()
        if diff.total_seconds() <= 0:
            return 0
        return diff.days


class ActivityLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True)
    username = db.Column(db.String(100), default='')
    role = db.Column(db.String(20), default='')
    action = db.Column(db.String(120), nullable=False)
    detail = db.Column(db.Text, default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if current_user.role != 'admin':
            flash('Bạn không có quyền truy cập chức năng này.', 'error')
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Fetch-Mode') == 'true':
                return jsonify({'success': False, 'message': 'Bạn không có quyền thực hiện thao tác này.'}), 403
            return redirect(url_for('index'))
        return func(*args, **kwargs)
    return wrapper


def not_tv_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        if current_user.role == 'tv':
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Fetch-Mode') == 'true':
                return jsonify({'success': False, 'message': 'Tài khoản TV không có quyền dùng chức năng này.'}), 403
            flash('Tài khoản TV không có quyền dùng chức năng này.', 'error')
            return redirect(url_for('tv_dashboard'))
        return func(*args, **kwargs)
    return wrapper


def create_default_admin():
    admin_username = os.environ.get('ADMIN_USERNAME', 'admin')
    admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')

    admin = User.query.filter_by(username=admin_username).first()

    if not admin:
        admin = User(
            username=admin_username,
            full_name='Quản trị viên',
            role='admin',
            active=True
        )
        db.session.add(admin)

    admin.active = True
    admin.role = 'admin'
    admin.full_name = 'Quản trị viên'
    admin.set_password(admin_password)

    db.session.commit()
    print(f"[AUTH] Admin ready: {admin_username} / {admin_password}")


with app.app_context():
    db_ready = False

    for i in range(15):
        try:
            db.create_all()
            db_ready = True
            print("[DB] Connected and create_all OK")
            break
        except Exception as e:
            print(f"[DB] Retry {i+1}/15: {e}")
            time.sleep(2)

    if db_ready:
        from sqlalchemy import inspect, text
        inspector = inspect(db.engine)

        user_tables = inspector.get_table_names()
        if 'standard_sub' in user_tables:
            columns = [col['name'] for col in inspector.get_columns('standard_sub')]
            if 'created_by_user_id' not in columns:
                db.session.execute(text("ALTER TABLE standard_sub ADD COLUMN created_by_user_id INTEGER"))
            if 'created_by_name' not in columns:
                db.session.execute(text("ALTER TABLE standard_sub ADD COLUMN created_by_name VARCHAR(100) DEFAULT ''"))
            db.session.commit()

        if 'premium_slot' in user_tables:
            columns = [col['name'] for col in inspector.get_columns('premium_slot')]
            if 'created_by_user_id' not in columns:
                db.session.execute(text("ALTER TABLE premium_slot ADD COLUMN created_by_user_id INTEGER"))
            if 'created_by_name' not in columns:
                db.session.execute(text("ALTER TABLE premium_slot ADD COLUMN created_by_name VARCHAR(100) DEFAULT ''"))
            db.session.commit()

        if 'account_vault' in user_tables:
            columns = [col['name'] for col in inspector.get_columns('account_vault')]
            if 'assigned_to' not in columns:
                db.session.execute(text("ALTER TABLE account_vault ADD COLUMN assigned_to VARCHAR(100) DEFAULT ''"))
            if 'assigned_to_user_id' not in columns:
                db.session.execute(text("ALTER TABLE account_vault ADD COLUMN assigned_to_user_id INTEGER"))
            if 'assigned_at' not in columns:
                db.session.execute(text("ALTER TABLE account_vault ADD COLUMN assigned_at DATETIME"))
            if 'last_checked_at' not in columns:
                db.session.execute(text("ALTER TABLE account_vault ADD COLUMN last_checked_at DATETIME"))
            if 'next_recheck_at' not in columns:
                db.session.execute(text("ALTER TABLE account_vault ADD COLUMN next_recheck_at DATETIME"))
            db.session.commit()

        create_default_admin()
    else:
        print("[DB] Database not ready, skipping init for now")

# --- HELPERS ---

def owns_item(item):
    if current_user.role == 'admin':
        return True
    owner_id = getattr(item, 'created_by_user_id', None)
    return owner_id == current_user.id


def ensure_item_access(item):
    if not item:
        abort(404)
    if not owns_item(item):
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Fetch-Mode') == 'true':
            return jsonify({'success': False, 'message': 'Bạn không có quyền thao tác dữ liệu này.'}), 403
        flash('Bạn không có quyền thao tác dữ liệu này.', 'error')
        return redirect(url_for('index'))
    return None


def get_visible_standard_query():
    query = StandardSub.query.filter_by(active=True)
    if current_user.role != 'admin':
        query = query.filter_by(created_by_user_id=current_user.id)
    return query


def get_visible_premium_slot_query(active_only=False):
    query = PremiumSlot.query
    if active_only:
        query = query.filter_by(active=True)
    if current_user.role != 'admin':
        query = query.filter_by(created_by_user_id=current_user.id)
    return query


def get_visible_premiums():
    accounts = PremiumAccount.query.order_by(PremiumAccount.id.desc()).all()
    if current_user.role == 'admin':
        return accounts
    visible = []
    for acc in accounts:
        has_own_slots = any(slot.active and slot.created_by_user_id == current_user.id for slot in acc.slots)
        if has_own_slots or acc.active_count < 5:
            visible.append(acc)
    return visible


def format_vault_status(acc, include_secrets=False):
    if include_secrets or (current_user.is_authenticated and current_user.role == 'admin'):
        password = acc.password
        cookies = acc.cookies or ""
    else:
        password = "********"
        cookies = "Ẩn với CTV"

    remaining_days = None
    remaining_text = ""

    if acc.next_recheck_at:
        now = datetime.now()
        diff = acc.next_recheck_at - now
        total_seconds = int(diff.total_seconds())

        if total_seconds <= 0:
            remaining_days = 0
            remaining_text = "Đến hạn gửi lại"
        else:
            days = diff.days
            hours = diff.seconds // 3600

            if days > 0:
                remaining_days = days
                remaining_text = f"Còn {days} ngày"
            else:
                remaining_days = 0
                remaining_text = f"Còn {hours} giờ"

    return {
        "id": acc.id,
        "email": acc.email,
        "password": password,
        "plan": acc.plan,
        "cookies": cookies,
        "status": acc.status,
        "assigned_to": acc.assigned_to or "",
        "assigned_at": acc.assigned_at.strftime('%d/%m/%Y %H:%M') if acc.assigned_at else "",
        "last_checked_at": acc.last_checked_at.strftime('%d/%m/%Y %H:%M') if acc.last_checked_at else "",
        "next_recheck_at": acc.next_recheck_at.strftime('%d/%m/%Y %H:%M') if acc.next_recheck_at else "",
        "remaining_days": remaining_days,
        "remaining_text": remaining_text
    }


def parse_cookie_blob(cookie_text):
    if not cookie_text:
        return []

    cookie_text = cookie_text.strip()

    # 1. Thử parse dạng JSON (Thường lấy từ EditThisCookie, J2TEAM...)
    try:
        data = json.loads(cookie_text)
        if isinstance(data, dict) and isinstance(data.get("cookies"), list):
            return data["cookies"]
        if isinstance(data, list):
            return data
    except Exception:
        pass

    cookies = []

    # 2. Thử parse dạng Netscape (Dạng xuất file text, ngăn cách bằng khoảng trắng/tab)
    # Dấu hiệu nhận biết: có chứa ký tự tab (\t) và chứa chữ netflix
    if "\t" in cookie_text and "netflix" in cookie_text.lower():
        lines = cookie_text.split('\n')
        for line in lines:
            # Bỏ qua dòng trống hoặc dòng ghi chú
            if not line.strip() or line.strip().startswith('#'):
                continue
            
            parts = line.split('\t')
            if len(parts) >= 7:
                cookies.append({
                    "domain": parts[0].strip(),
                    "path": parts[2].strip(),
                    "name": parts[5].strip(),
                    "value": parts[6].strip()
                })
        if cookies:
            return cookies

    # 3. Thử parse dạng chuỗi Header thông thường (VD: NetflixId=123; SecureNetflixId=456)
    parts = cookie_text.split(";")
    for part in parts:
        if "=" not in part:
            continue
        
        # Cắt chính xác tại dấu "=" đầu tiên, lấy toàn bộ dữ liệu ở phần giá trị phía sau
        name, value = part.strip().split("=", 1)

        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": ".netflix.com",
            "path": "/"
        })

    return cookies

def convert_cookies_for_playwright(raw_cookies):
    converted = []
    for c in raw_cookies:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        value = c.get("value")
        domain = c.get("domain")
        path = c.get("path", "/")
        if not name or not value or not domain:
            continue
        item = {
            "name": name,
            "value": value,
            "domain": domain,
            "path": path
        }
        expires = c.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            item["expires"] = expires
        if "httpOnly" in c:
            item["httpOnly"] = bool(c["httpOnly"])
        if "secure" in c:
            item["secure"] = bool(c["secure"])
        same_site = c.get("sameSite")
        if same_site in ["Lax", "None", "Strict"]:
            item["sameSite"] = same_site
        converted.append(item)
    return converted


def log_activity(action, detail=''):
    if not current_user.is_authenticated:
        return
    try:
        db.session.add(ActivityLog(
            user_id=current_user.id,
            username=current_user.full_name or current_user.username,
            role=current_user.role,
            action=action,
            detail=detail
        ))
        db.session.commit()
    except Exception as ex:
        db.session.rollback()
        print(f"[ACTIVITY] log failed: {ex}")


def check_netflix_cookie_live(cookie_text):
    raw_cookies = parse_cookie_blob(cookie_text)
    cookies = convert_cookies_for_playwright(raw_cookies)

    if not cookies:
        return 'DEAD', "Cookie rỗng hoặc sai định dạng"

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            context.add_cookies(cookies)
            page = context.new_page()
            
            page.goto("https://www.netflix.com/browse", wait_until="domcontentloaded", timeout=60000)
            time.sleep(5)
            current_url = (page.url or "").lower()
            html = (page.content() or "").lower()
            is_login = ("/login" in current_url) or ("/signin" in current_url)
            live_signals = ["profilesgate", "browse", "accountmenuitem", "netflix"]
            
            if is_login:
                browser.close()
                return 'DEAD', "Cookie đã chết, bị chuyển về trang login" # <-- BÁO DEAD
            
            if any(sig in html for sig in live_signals) or "/browse" in current_url:
                try:
                    page.goto("https://www.netflix.com/YourAccount", wait_until="domcontentloaded", timeout=60000)
                    time.sleep(4)
                    body_text = (page.inner_text("body") or "").lower()
                    
                    # Danh sách từ khóa nhận diện tài khoản hết hạn / hold / chưa thanh toán (Bao gồm EN & VI)
                    # Bộ từ khóa nhận diện tài khoản hết hạn / hold / chưa thanh toán (Bản Ultimate + Account Hold)
                    expired_keywords = [
                        # --- NHÓM TỪ KHÓA TẠM NGƯNG TÀI KHOẢN (ACCOUNT HOLD) MỚI THÊM ---
                        "account is on hold", "account suspended", "retry payment",
                        "tu cuenta está suspendida", "cuenta suspendida", "reintentar pago",
                        "tài khoản đang bị tạm ngưng", "tài khoản của bạn bị tạm ngưng", "thử thanh toán lại",
                        "sua conta está suspensa", "conta suspensa", "tentar o pagamento novamente",
                        "votre compte est suspendu", "réessayer le paiement",
                        
                        # --- CHÂU MỸ & CHÂU ÂU (Phổ biến) ---
                        # 1. Tiếng Anh (English - Global)
                        "we can't process your payment", "process your payment", "restart your membership", 
                        "update payment", "finish sign-up", "payment is past due",
                        
                        # 2. Tiếng Tây Ban Nha (Spanish - LATAM & Spain)
                        "procesar tu pago", "procesar el pago", "reiniciar tu membresía", 
                        "actualizar pago", "finalizar registro", "actualiza tu forma de pago",
                        
                        # 3. Tiếng Bồ Đào Nha (Portuguese - Brazil & Portugal)
                        "processar seu pagamento", "processar o pagamento", "reiniciar assinatura", 
                        "atualizar forma de pagamento", "reiniciar a sua adesão", "atualize o pagamento",
                        
                        # 4. Tiếng Pháp (French)
                        "traiter votre paiement", "réactiver votre abonnement", 
                        "mettre à jour le paiement", "mode de paiement",
                        
                        # 5. Tiếng Đức (German)
                        "zahlung nicht verarbeiten", "mitgliedschaft reaktivieren", 
                        "zahlungsart aktualisieren", "zahlung fehlgeschlagen",
                        
                        # 6. Tiếng Ý (Italian)
                        "elaborare il pagamento", "riattiva il tuo abbonamento", 
                        "aggiorna il metodo", "aggiorna il pagamento",

                        # --- ĐÔNG ÂU & NGA ---
                        # 7. Tiếng Nga (Russian)
                        "не удалось обработать ваш платеж", "возобновить подписку", "обновить способ оплаты",
                        
                        # 8. Tiếng Ukraina (Ukrainian)
                        "не вдалося обробити ваш платіж", "відновити членство", "оновити спосіб оплати",
                        
                        # 9. Tiếng Ba Lan (Polish)
                        "przetworzyć twojej płatności", "odnów członkostwo", "zaktualizuj metodę płatności",
                        
                        # 10. Tiếng Romania (Romanian)
                        "procesa plata", "repornește abonamentul", "actualizează metoda de plată",
                        
                        # 11. Tiếng Hungary (Hungarian)
                        "feldolgozni a fizetést", "tagság újraindítása", "fizetési mód frissítése",
                        
                        # 12. Tiếng Séc (Czech)
                        "zpracovat vaši platbu", "obnovit členství", "aktualizovat platební metodu",
                        
                        # 13. Tiếng Slovak (Slovak)
                        "spracovať vašu platbu", "obnoviť členstvo", "aktualizovať spôsob platby",
                        
                        # 14. Tiếng Hy Lạp (Greek)
                        "επεξεργαστούμε την πληρωμή", "επανέναρξη της συνδρομής", "ενημέρωση πληρωμής",
                        
                        # 15. Tiếng Croatia / Serbia (Croatian / Serbian)
                        "obraditi vašu uplatu", "ponovno pokrenite članstvo", "ažurirajte plaćanje",
                        "obradimo vašu uplatu", "ponovo pokrenite članstvo",
                        
                        # 16. Tiếng Bulgaria (Bulgarian)
                        "обработим плащането", "подновете членството", "актуализирайте плащането",

                        # --- BẮC ÂU & TÂY ÂU KHÁC ---
                        # 17. Tiếng Hà Lan (Dutch)
                        "betaling niet verwerken", "lidmaatschap opnieuw starten", "betaalgegevens bijwerken",
                        
                        # 18. Tiếng Thụy Điển (Swedish)
                        "behandla din betalning", "starta om ditt medlemskap", "uppdatera betalningsmetod",
                        
                        # 19. Tiếng Na Uy (Norwegian)
                        "behandle betalingen", "start medlemskapet på nytt", "oppdater betalingsmåte",
                        
                        # 20. Tiếng Đan Mạch (Danish)
                        "behandle din betaling", "genstart dit medlemskab", "opdater betaling",
                        
                        # 21. Tiếng Phần Lan (Finnish)
                        "käsitellä maksuasi", "aloita jäsenyys uudelleen", "päivitä maksutapa",

                        # --- CHÂU Á & THÁI BÌNH DƯƠNG ---
                        # 22. Tiếng Việt (Vietnamese)
                        "lỗi thanh toán", "có vấn đề với thanh toán", "cập nhật thanh toán", 
                        "khôi phục tư cách thành viên", "hoàn tất đăng ký", "xử lý thanh toán",
                        
                        # 23. Tiếng Trung (Chinese - Giản/Phồn thể)
                        "无法处理您的付款", "無法處理您的付款", "重新启动", "重新啟動", "更新付款", "更新付款方式",
                        
                        # 24. Tiếng Nhật (Japanese)
                        "お支払いを処理できません", "メンバーシップを再開", "お支払い方法の更新",
                        
                        # 25. Tiếng Hàn (Korean)
                        "결제를 처리할 수 없습니다", "멤버십 재시작", "결제 수단 업데이트",
                        
                        # 26. Tiếng Thái (Thai)
                        "ดำเนินการชำระเงิน", "เริ่มการเป็นสมาชิก", "อัปเดตการชำระเงิน",
                        
                        # 27. Tiếng Indonesia (Indonesian)
                        "memproses pembayaran", "mulai ulang keanggotaan", "perbarui pembayaran",
                        
                        # 28. Tiếng Mã Lai (Malay)
                        "memproses bayaran", "mulakan semula keahlian", "kemas kini pembayaran",
                        
                        # 29. Tiếng Tagalog (Filipino)
                        "iproseso ang iyong pagbabayad", "i-restart ang iyong membership", "i-update ang pagbabayad",
                        
                        # 30. Tiếng Hindi (Indian)
                        "भुगतान को प्रोसेस नहीं", "मेंबरशिप दोबारा शुरू", "भुगतान का तरीका अपडेट",
                        
                        # 31. Tiếng Tamil & Telugu & Bengali (India)
                        "கட்டணத்தைச் செயலாக்க", "பேமெண்ட்டைப் புதுப்பி", 
                        "చెల్లింపును ప్రాసెస్", "చెల్లింపును అప్‌డేట్",
                        "পেমেন্ট প্রসেস", "পেমেন্ট আপডেট",

                        # --- TRUNG ĐÔNG & CHÂU PHI ---
                        # 32. Tiếng Thổ Nhĩ Kỳ (Turkish)
                        "ödemenizi işleme", "üyeliğinizi yeniden başlatın", "ödeme yöntemini", "ödeme sorunu",
                        
                        # 33. Tiếng Ả Rập (Arabic)
                        "معالجة عملية الدفع", "إعادة تشغيل عضويتك", "تحديث طريقة الدفع",
                        
                        # 34. Tiếng Do Thái (Hebrew)
                        "לא הצלחנו לעבד את התשלום", "חידוש המינוי", "עדכון פרטי התשלום",
                        
                        # 35. Tiếng Swahili (Châu Phi)
                        "kushughulikia malipo", "anza upya uanachama", "sasisha malipo",
                        
                        # 36. Tiếng Afrikaans (Nam Phi)
                        "betaling verwerk nie", "herbegin jou lidmaatskap", "werk betaling op"
                    ]
                    for kw in expired_keywords:
                        if kw in body_text:
                            browser.close()
                            return 'EXPIRED', f"Tài khoản HẾT HẠN / HOLD (Phát hiện: {kw})" # <-- BÁO HẾT HẠN
                    
                    browser.close()
                    return 'LIVE', "Cookie hoạt động & Đang có gói cước" # <-- BÁO LIVE
                    
                except Exception as inner_e:
                    print(f"[Check Account Error]: {inner_e}")
                    browser.close()
                    return 'LIVE', "Cookie hoạt động (Lỗi khi check hạn)"

            browser.close()
            return 'DEAD', "Không xác định được trạng thái cookie, coi như Dead"
    except Exception as e:
        try:
            if context: context.close()
            if browser: browser.close()
        except: pass
        return 'DEAD', f"Lỗi Playwright: {str(e)}"


def login_netflix_tv(cookie_text, tv_code):
    raw_cookies = parse_cookie_blob(cookie_text)
    cookies = convert_cookies_for_playwright(raw_cookies)

    if not cookies:
        return False, "Cookie rỗng hoặc sai định dạng"

    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, slow_mo=200)
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            context.add_cookies(cookies)
            page = context.new_page()

            page.goto("https://www.netflix.com/tv8", wait_until="domcontentloaded", timeout=60000)
            time.sleep(4)
            current_url = (page.url or "").lower()

            if "/login" in current_url or "/signin" in current_url:
                browser.close()
                return False, "Cookie đã chết, bị chuyển hướng về trang đăng nhập."

            try:
                page.wait_for_selector('input', timeout=10000)
                inputs = page.locator('input:not([type="hidden"])').all()

                if len(inputs) >= 8:
                    for i, char in enumerate(tv_code):
                        if i < len(inputs):
                            inputs[i].fill(char)
                elif len(inputs) > 0:
                    inputs[0].fill(tv_code)
                else:
                    page.keyboard.type(tv_code, delay=150)

                page.locator('button[data-uia="tv-code-submit-button"], button[type="submit"], button.btn-submit').first.click()
                time.sleep(6)

                final_url = (page.url or "").lower()

                if "tv/out/success" in final_url:
                    browser.close()
                    return True, "Đăng nhập TV thành công!"

                error_loc = page.locator('.ui-message-error, [data-uia="error-message-container"]')
                err_text = "Mã không hợp lệ hoặc đã hết thời gian."
                if error_loc.is_visible():
                    err_text = error_loc.first.inner_text()

                browser.close()
                return False, f"Thất bại: {err_text}"

            except Exception as ex:
                browser.close()
                return False, f"Lỗi thao tác điền mã TV: {str(ex)}"
    except Exception as e:
        try:
            if context:
                context.close()
            if browser:
                browser.close()
        except Exception:
            pass
        return False, f"Lỗi Playwright: {str(e)}"


def auto_recheck_assigned_accounts():
    with app.app_context():
        now = datetime.now()
        due_accounts = AccountVault.query.filter(
            AccountVault.status.in_(["Đã Cấp", "Đã Live"]),
            AccountVault.next_recheck_at.isnot(None),
            AccountVault.next_recheck_at <= now
        ).all()

        if not due_accounts:
            print("[AUTO RESET] Không có account nào đến hạn gửi lại.")
            return

        print(f"[AUTO RESET] Bắt đầu reset {len(due_accounts)} account đến hạn...")

        for acc in due_accounts:
            print(f"[AUTO RESET] Reset: {acc.email}")

            acc.status = "Offline"
            acc.assigned_to = ""
            acc.assigned_to_user_id = None
            acc.assigned_at = None
            acc.last_checked_at = None
            acc.next_recheck_at = None

        db.session.commit()
        print("[AUTO RESET] Hoàn tất reset account đến hạn.")
# --- AUTH ROUTES ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        if current_user.role == 'tv':
            return redirect(url_for('tv_dashboard'))
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        user = User.query.filter_by(username=username).first()

        if not user or not user.active or not user.check_password(password):
            flash('Sai tài khoản hoặc mật khẩu.', 'error')
            return render_template('login.html')

        login_user(user)
        log_activity('Đăng nhập', f"Tài khoản {user.username} đăng nhập hệ thống")
        flash(f'Đăng nhập thành công. Xin chào {user.full_name or user.username}!', 'success')

        if user.role == 'tv':
            return redirect(url_for('tv_dashboard'))
        return redirect(url_for('index'))

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Bạn đã đăng xuất.', 'success')
    return redirect(url_for('login'))


@app.route('/create_user', methods=['POST'])
@login_required
@admin_required
def create_user():
    username = (request.form.get('username') or '').strip()
    password = request.form.get('password') or ''
    full_name = (request.form.get('full_name') or '').strip()
    role = (request.form.get('role') or 'ctv').strip().lower()

    if role not in ['admin', 'ctv', 'tv']:
        flash('Role không hợp lệ.', 'error')
        return redirect(url_for('index'))

    if not username or not password:
        flash('Vui lòng nhập username và password.', 'error')
        return redirect(url_for('index'))

    if User.query.filter_by(username=username).first():
        flash('Username đã tồn tại.', 'error')
        return redirect(url_for('index'))

    user = User(username=username, full_name=full_name, role=role, active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    log_activity('Tạo tài khoản', f"Tạo user {username} với quyền {role}")
    flash(f'Đã tạo tài khoản {role.upper()} cho {username}.', 'success')
    return redirect(url_for('index') + '#view-users')


# --- TV ONLY ROUTES ---
@app.route('/tv')
@login_required
def tv_dashboard():
    if current_user.role != 'tv':
        return redirect(url_for('index'))
    return render_template('tv_only.html')


# --- APP ROUTES ---
@app.route('/')
@login_required
def index():
    if current_user.role == 'tv':
        return redirect(url_for('tv_dashboard'))

    standards_active = get_visible_standard_query().order_by(StandardSub.expiry_date.asc()).all()
    premiums = get_visible_premiums()

    todos = []
    for s in standards_active:
        if s.days_left <= 5:
            todos.append({
                'type': 'standard',
                'id': s.id,
                'name': s.customer_name,
                'sub_text': f"Khách lẻ • {s.source}",
                'days': s.days_left,
                'expiry': s.expiry_date,
                'status': s.payment_status,
                'contact_link': s.contact_link,
                'source': s.source,
                'created_by_name': s.created_by_name or ''
            })

    premium_todo_slots = get_visible_premium_slot_query(active_only=True).order_by(PremiumSlot.expiry_date.asc()).all()
    premium_acc_map = {acc.id: acc.email for acc in PremiumAccount.query.all()}
    for slot in premium_todo_slots:
        if slot.days_left <= 5:
            todos.append({
                'type': 'premium',
                'id': slot.id,
                'name': slot.customer_name,
                'sub_text': f"Profile: {slot.profile_name} (Acc: {premium_acc_map.get(slot.account_id, '')})",
                'days': slot.days_left,
                'expiry': slot.expiry_date,
                'status': slot.payment_status,
                'contact_link': slot.contact_link,
                'source': slot.source,
                'created_by_name': slot.created_by_name or ''
            })

    todos.sort(key=lambda x: x['days'])

    master_list = []
    if current_user.role == 'admin':
        all_standards = StandardSub.query.order_by(StandardSub.expiry_date.desc()).all()
        all_slots = PremiumSlot.query.order_by(PremiumSlot.expiry_date.desc()).all()

        for s in all_standards:
            master_list.append({
                'name': s.customer_name,
                'package': 'Gói Phổ Thông',
                'expiry': s.expiry_date,
                'days_left': s.days_left,
                'is_active': s.active,
                'payment': s.payment_status
            })

        for slot in all_slots:
            master_list.append({
                'name': slot.customer_name,
                'package': 'Gói Cao Cấp',
                'expiry': slot.expiry_date,
                'days_left': slot.days_left,
                'is_active': slot.active,
                'payment': slot.payment_status
            })

    vault_accounts = AccountVault.query.order_by(AccountVault.id.desc()).all() if current_user.role == 'admin' else []
    my_fetched_accounts = AccountVault.query.filter_by(assigned_to_user_id=current_user.id).order_by(AccountVault.assigned_at.desc()).all() if current_user.role != 'admin' else []
    users = User.query.order_by(User.created_at.desc()).all() if current_user.role == 'admin' else []
    activity_logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(200).all() if current_user.role == 'admin' else []

    return render_template(
        'index.html',
        standards=standards_active,
        premiums=premiums,
        todos=todos,
        master_list=master_list,
        vault_accounts=vault_accounts,
        my_fetched_accounts=my_fetched_accounts,
        users=users,
        activity_logs=activity_logs,
        today=date.today()
    )


@app.route('/stop_service/<type>/<int:item_id>')
@login_required
@not_tv_required
def stop_service(type, item_id):
    item = StandardSub.query.get(item_id) if type == 'standard' else PremiumSlot.query.get(item_id)
    denied = ensure_item_access(item)
    if denied:
        return denied
    item.active = False
    db.session.commit()
    log_activity('Ngừng dịch vụ', f"{'Gói phổ thông' if type == 'standard' else 'Slot premium'}: {item.customer_name}")
    flash(f'Đã chuyển "{item.customer_name}" sang danh sách lưu trữ.', 'warning')
    return redirect(url_for('index'))


@app.route('/reset_vault/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def reset_vault(item_id):
    acc = AccountVault.query.get(item_id)
    if not acc:
        return jsonify({'success': False, 'message': 'Không tìm thấy tài khoản.'}), 404

    acc.status = "Chưa Check"
    acc.last_checked_at = None
    acc.assigned_to = ""
    acc.assigned_to_user_id = None
    acc.assigned_at = None
    acc.next_recheck_at = None

    db.session.commit()
    return jsonify({'success': True, 'message': f'Đã reset tài khoản {acc.email} về trạng thái chưa check.'})


@app.route('/update_status/<type>/<int:item_id>', methods=['POST'])
@login_required
@not_tv_required
def update_status(type, item_id):
    if request.is_json:
        data = request.get_json() or {}
        new_status = data.get('payment_status')
    else:
        new_status = request.form.get('payment_status')

    item = StandardSub.query.get(item_id) if type == 'standard' else PremiumSlot.query.get(item_id)
    denied = ensure_item_access(item)
    if denied:
        return denied

    item.payment_status = new_status
    db.session.commit()
    log_activity('Cập nhật trạng thái thu tiền', f"{item.customer_name}: {new_status}")

    if request.is_json:
        return jsonify({'success': True})
    return redirect(url_for('index'))


@app.route('/quick_renew/<type>/<int:item_id>', methods=['POST'])
@login_required
@not_tv_required
def quick_renew(type, item_id):
    item = StandardSub.query.get(item_id) if type == 'standard' else PremiumSlot.query.get(item_id)
    denied = ensure_item_access(item)
    if denied:
        return denied
    try:
        months = int(request.form.get('duration', 1))
    except Exception:
        months = 1

    if item.expiry_date >= date.today():
        item.expiry_date = item.expiry_date + timedelta(days=months * 30)
    else:
        item.expiry_date = date.today() + timedelta(days=months * 30)

    item.payment_status = 'Chưa thu'
    item.active = True
    db.session.commit()
    log_activity('Gia hạn nhanh', f"{item.customer_name}: +{months} tháng")

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.headers.get('Fetch-Mode') == 'true':
        return jsonify({'success': True, 'msg': f'Đã gia hạn {months} tháng cho {item.customer_name}'})

    flash(f'Đã gia hạn {months} tháng!', 'success')
    return redirect(url_for('index'))


@app.route('/edit_premium_slot/<int:item_id>', methods=['POST'])
@login_required
@not_tv_required
def edit_premium_slot(item_id):
    slot = PremiumSlot.query.get(item_id)
    denied = ensure_item_access(slot)
    if denied:
        return denied
    slot.customer_name = request.form['customer_name']
    slot.profile_name = request.form['profile_name']
    slot.source = request.form['source']
    slot.contact_link = request.form.get('contact_link', '')
    slot.expiry_date = datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date()
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/edit_premium_acc/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def edit_premium_acc(item_id):
    acc = PremiumAccount.query.get(item_id)
    if acc:
        new_email = request.form.get('email')
        existing = PremiumAccount.query.filter_by(email=new_email).first()
        if existing and existing.id != item_id:
            flash('Email này đã tồn tại trong hệ thống!', 'error')
        else:
            acc.email = new_email
            db.session.commit()
    return redirect(url_for('index'))


@app.route('/add_standard', methods=['POST'])
@login_required
@not_tv_required
def add_standard():
    item = StandardSub(
        customer_name=request.form['name'],
        source=request.form['source'],
        contact_link=request.form.get('contact_link', ''),
        expiry_date=datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date(),
        created_by_user_id=current_user.id,
        created_by_name=current_user.full_name or current_user.username
    )
    db.session.add(item)
    db.session.commit()
    log_activity('Thêm gói phổ thông', f"Khách: {item.customer_name} - Nguồn: {item.source}")
    return redirect(url_for('index'))


@app.route('/delete_standard/<int:item_id>')
@login_required
@admin_required
def delete_standard(item_id):
    db.session.delete(StandardSub.query.get(item_id))
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/add_premium_account', methods=['POST'])
@login_required
@admin_required
def add_premium_account():
    if not PremiumAccount.query.filter_by(email=request.form['email']).first():
        db.session.add(PremiumAccount(email=request.form['email']))
        db.session.commit()
    return redirect(url_for('index'))


@app.route('/add_premium_slot/<int:acc_id>', methods=['POST'])
@login_required
@not_tv_required
def add_premium_slot(acc_id):
    acc = PremiumAccount.query.get_or_404(acc_id)
    active_slots = sum(1 for slot in acc.slots if slot.active)
    if active_slots >= 5:
        flash('Tài khoản này đã đủ 5 slot active.', 'error')
        return redirect(url_for('index') + '#view-premium')
    slot = PremiumSlot(
        account_id=acc_id,
        customer_name=request.form['customer_name'],
        profile_name=request.form['profile_name'],
        source=request.form['source'],
        contact_link=request.form.get('contact_link', ''),
        expiry_date=datetime.strptime(request.form['expiry_date'], '%Y-%m-%d').date(),
        created_by_user_id=current_user.id,
        created_by_name=current_user.full_name or current_user.username
    )
    db.session.add(slot)
    db.session.commit()
    log_activity('Thêm slot premium', f"Khách: {slot.customer_name} - Profile: {slot.profile_name} - Acc: {acc.email}")
    return redirect(url_for('index') + '#view-premium')


@app.route('/delete_slot/<int:item_id>')
@login_required
@admin_required
def delete_slot(item_id):
    db.session.delete(PremiumSlot.query.get(item_id))
    db.session.commit()
    return redirect(url_for('index'))


@app.route('/delete_premium_acc/<int:item_id>')
@login_required
@admin_required
def delete_premium_acc(item_id):
    db.session.delete(PremiumAccount.query.get(item_id))
    db.session.commit()
    return redirect(url_for('index'))


def normalize_plan(plan_raw):
    if not plan_raw:
        return "Unknown Plan"

    p = str(plan_raw).strip().lower()

    if "premium" in p or p in ["pre", "premium no ads"]:
        return "Premium"
    if "standard" in p:
        return "Standard"
    if "basic" in p:
        return "Basic"

    return str(plan_raw).strip() or "Unknown Plan"


@app.route('/import_excel', methods=['POST'])
@login_required
@admin_required
def import_excel():
    if 'excel_file' not in request.files:
        flash('Không tìm thấy file!', 'error')
        return redirect(url_for('index') + '#view-vault')

    file = request.files['excel_file']
    if file.filename == '':
        flash('Bạn chưa chọn file Excel nào.', 'error')
        return redirect(url_for('index') + '#view-vault')

    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        try:
            wb = openpyxl.load_workbook(file)
            sheet = wb.active
            count = 0
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if row[0] and row[1]:
                    email = str(row[0]).strip()
                    password = str(row[1]).strip()
                    plan = normalize_plan(row[2]) if len(row) > 2 else "Unknown Plan"
                    cookies = str(row[3]).strip() if len(row) > 3 and row[3] else ''
                    if not AccountVault.query.filter_by(email=email).first():
                        new_acc = AccountVault(
                            email=email,
                            password=password,
                            plan=plan,
                            cookies=cookies,
                            status='Chưa Check'
                        )
                        db.session.add(new_acc)
                        count += 1
            db.session.commit()
            flash(f'Đã import thành công {count} tài khoản mới vào kho!', 'success')
        except Exception as e:
            flash(f'Lỗi khi đọc file Excel: {str(e)}', 'error')
    else:
        flash('Vui lòng chỉ upload file đuôi .xlsx hoặc .xls', 'error')

    return redirect(url_for('index') + '#view-vault')


@app.route('/delete_vault/<int:item_id>')
@login_required
@admin_required
def delete_vault(item_id):
    acc = AccountVault.query.get(item_id)
    if acc:
        db.session.delete(acc)
        db.session.commit()
    return redirect(url_for('index') + '#view-vault')


@app.route('/check_account/<int:item_id>', methods=['POST'])
@login_required
@admin_required
def check_account(item_id):
    acc = AccountVault.query.get(item_id)
    if not acc:
        return jsonify({'success': False, 'message': 'Không tìm thấy tài khoản.'}), 404

    # Gọi hàm để lấy 1 trong 3 trạng thái
    status_code, check_msg = check_netflix_cookie_live(acc.cookies)
    acc.last_checked_at = datetime.now()
    
    # Gán trạng thái vào DB tương ứng
    if status_code == 'LIVE':
        acc.status = 'Đã Live'
    elif status_code == 'EXPIRED':
        acc.status = 'Hết hạn'
    else:
        acc.status = 'Dead'
        
    db.session.commit()
    return jsonify({'success': True, 'message': f'{acc.status} - {check_msg}', 'account': format_vault_status(acc)})


@app.route('/fetch_account', methods=['POST'])
@login_required
@not_tv_required
def fetch_account():
    try:
        data = request.get_json(silent=True) or {}
        plan = (data.get('plan') or '').strip()

        query = AccountVault.query.filter(AccountVault.status.in_(['Offline', 'Chưa Check', 'Đã Live']))

        if current_user.role != 'admin':
            query = query.filter((AccountVault.assigned_to_user_id.is_(None)) | (AccountVault.assigned_to_user_id == current_user.id))

        if plan: query = query.filter(AccountVault.plan.ilike(f"%{plan}%"))

        candidates = query.order_by(AccountVault.id.asc()).all()

        if not candidates:
            return jsonify({'success': False, 'message': 'Không còn tài khoản phù hợp trong kho.'}), 404

        for acc in candidates:
            status_code, check_msg = check_netflix_cookie_live(acc.cookies)
            acc.last_checked_at = datetime.now()

            if status_code == 'LIVE':
                now = datetime.now()
                acc.status = 'Đã Cấp'
                acc.assigned_to_user_id = current_user.id
                acc.assigned_to = current_user.full_name or current_user.username
                acc.assigned_at = now
                acc.next_recheck_at = now + timedelta(days=10)
                db.session.commit()
                log_activity('Lấy account kho', f"Lấy {acc.email} - Plan: {acc.plan}")

                return jsonify({
                    'success': True,
                    'message': f'Lấy tài khoản thành công. {check_msg}',
                    'account': format_vault_status(acc, include_secrets=True)
                })
            
            # Nếu phát hiện hết hạn hoặc chết thì đánh dấu vào DB rồi lặp qua acc tiếp theo
            elif status_code == 'EXPIRED':
                acc.status = 'Hết hạn'
                db.session.commit()
            else:
                acc.status = 'Dead'
                db.session.commit()

        return jsonify({'success': False, 'message': 'Đã quét qua các acc nhưng đều Dead hoặc Hết hạn. Hãy nạp thêm kho!'}), 404

    except Exception as e:
        return jsonify({'success': False, 'message': f'Lỗi khi lấy tài khoản: {str(e)}'}), 500


@app.route('/login_tv_code', methods=['POST'])
@login_required
def login_tv_code():
    data = request.get_json(silent=True) or {}
    tv_code = data.get('tv_code', '').strip().replace(" ", "")

    if len(tv_code) != 8:
        return jsonify({'success': False, 'message': 'Mã TV phải có đúng 8 ký tự.'})

    query = AccountVault.query.filter(AccountVault.status.in_(['Offline', 'Chưa Check', 'Đã Live']))

    if current_user.role != 'admin':
        query = query.filter((AccountVault.assigned_to_user_id.is_(None)) | (AccountVault.assigned_to_user_id == current_user.id))

    candidates = query.order_by(AccountVault.id.asc()).all()

    if not candidates:
        return jsonify({'success': False, 'message': 'Không có tài khoản khả dụng trong kho.'})

    for acc in candidates:
        is_success, msg = login_netflix_tv(acc.cookies, tv_code)

        if is_success:
            now = datetime.now()
            acc.status = 'Đã Cấp'
            acc.last_checked_at = now
            acc.assigned_to_user_id = current_user.id
            acc.assigned_to = current_user.full_name or current_user.username
            acc.assigned_at = now
            acc.next_recheck_at = now + timedelta(days=10)

            db.session.commit()
            log_activity('Duyệt mã TV', f"Thành công với account {acc.email} và mã {tv_code}")
            return jsonify({
                'success': True,
                'message': f'Duyệt TV thành công trên Email: {acc.email}! {msg}'
            })

        elif "chết" in msg.lower() or "login" in msg.lower():
            acc.status = 'Dead'
            acc.last_checked_at = datetime.now()
            db.session.commit()
            continue
        else:
            return jsonify({'success': False, 'message': f'Duyệt thất bại: {msg}'})

    return jsonify({'success': False, 'message': 'Toàn bộ tài khoản khả dụng đều đã chết Cookie, hãy nạp thêm kho.'})


@app.route('/assign_account/<int:item_id>', methods=['POST'])
@login_required
@not_tv_required
def assign_account(item_id):
    acc = AccountVault.query.get(item_id)
    if not acc:
        return jsonify({'success': False, 'message': 'Không tìm thấy tài khoản.'}), 404
    if acc.status == 'Đã Cấp' and acc.assigned_to_user_id not in [None, current_user.id]:
        return jsonify({'success': False, 'message': 'Tài khoản này đã được cấp cho người khác.'}), 400

    data = request.get_json(silent=True) or {}
    assigned_to = (data.get('assigned_to') or '').strip()
    if current_user.role == 'admin':
        assigned_to = assigned_to or current_user.username
    else:
        assigned_to = assigned_to or (current_user.full_name or current_user.username)

    now = datetime.now()
    acc.status = 'Đã Cấp'
    acc.assigned_to = assigned_to
    acc.assigned_to_user_id = current_user.id
    acc.assigned_at = now
    acc.next_recheck_at = now + timedelta(days=10)
    db.session.commit()
    log_activity('Cấp account thủ công', f"Cấp {acc.email} cho {assigned_to}")
    return jsonify({'success': True, 'message': f'Đã cấp tài khoản {acc.email}', 'account': format_vault_status(acc, include_secrets=True)})


scheduler = BackgroundScheduler()
scheduler.add_job(
    func=auto_recheck_assigned_accounts,
    trigger='interval',
    hours=1,
    id='auto_recheck_assigned_accounts',
    replace_existing=True
)


if __name__ == '__main__':
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler.start()
    app.run(debug=True, port=5000)
