import os
import json
import sqlite3
import stripe
from fastapi import FastAPI, Request, Form, Response, Cookie, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.generativeai as genai

# Load configurations
CONFIG_FILE = "config.json"
with open(CONFIG_FILE, "r") as f:
    config = json.load(f)

# Initialize Stripe
stripe.api_key = config.get("stripe_api_key")

# Initialize Gemini
genai.configure(api_key=config.get("gemini_api_key"))

DB_FILE = "database.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name TEXT NOT NULL,
        role TEXT DEFAULT 'client',
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        subscription_status TEXT DEFAULT 'unpaid'
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS websites (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        business_name TEXT,
        industry TEXT,
        inputs_json TEXT,
        generated_content_json TEXT,
        domain_requested TEXT,
        domain_status TEXT DEFAULT 'pending',
        published_status TEXT DEFAULT 'staging',
        FOREIGN KEY(user_id) REFERENCES users(id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        website_id INTEGER,
        sender TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(website_id) REFERENCES websites(id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS demo_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL,
        business_name TEXT NOT NULL,
        industry TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # Ensure all columns exist for demo_requests
    columns_to_add = [
        ("phone", "TEXT"),
        ("tone", "TEXT"),
        ("strengths", "TEXT"),
        ("target", "TEXT"),
        ("message", "TEXT")
    ]
    for col_name, col_type in columns_to_add:
        try:
            cursor.execute(f"ALTER TABLE demo_requests ADD COLUMN {col_name} {col_type}")
        except sqlite3.OperationalError:
            # Column already exists
            pass
            
    # Create default admin if not exists
    cursor.execute("SELECT * FROM users WHERE email = 'admin@ranse.com'")
    if not cursor.fetchone():
        cursor.execute(
            "INSERT INTO users (email, password_hash, name, role, subscription_status) VALUES (?, ?, ?, ?, ?)",
            ("admin@ranse.com", "admin123", "システム管理者", "admin", "active")
        )
    conn.commit()
    conn.close()

# Run database initialization
init_db()

app = FastAPI(title="L'Anse Creative Web Portal")

# Ensure templates and static directories exist
os.makedirs("templates", exist_ok=True)
os.makedirs("static", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def object_lookup(json_str, key):
    try:
        data = json.loads(json_str)
        return data.get(key, "")
    except Exception:
        return ""

templates.env.filters["object_lookup"] = object_lookup

# Helper to get db connection
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

# Helper to get current user from cookie
def get_current_user(user_id: str = Cookie(None)):
    if not user_id:
        return None
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

# ----------------- PUBLIC PAGES -----------------

@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request, user_id: str = Cookie(None)):
    current_user = get_current_user(user_id)
    return templates.TemplateResponse(request, "index.html", {"user": current_user})

@app.get("/proposal", response_class=HTMLResponse)
async def read_proposal(request: Request):
    return templates.TemplateResponse(request, "proposal.html", {})

@app.post("/demo-request")
async def handle_demo_request(name: str = Form(...), email: str = Form(...), business_name: str = Form(...), industry: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO demo_requests (name, email, business_name, industry) VALUES (?, ?, ?, ?)",
        (name, email, business_name, industry)
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/?demo_success=true", status_code=303)

@app.post("/contact")
async def handle_contact(
    name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(""),
    company: str = Form(""),
    message: str = Form(""),
    industry: str = Form(""),
    tone: str = Form(""),
    strengths: str = Form(""),
    target: str = Form("")
):
    # Log email dispatch to susumu.miyashita@coedo-music.jp
    print("\n" + "="*50)
    print("【EMAIL SEND SIMULATION】")
    print(f"To: susumu.miyashita@coedo-music.jp")
    print(f"From: {email}")
    print(f"Subject: 新規ホームページ・無料デモ制作の相談 (お名前: {name} 様)")
    print(f"Content:")
    print(f"  お名前: {name}")
    print(f"  メールアドレス: {email}")
    print(f"  電話番号: {phone}")
    print(f"  店舗名・会社名: {company}")
    print(f"  業種: {industry}")
    print(f"  希望テイスト: {tone}")
    print(f"  お店の強み: {strengths}")
    print(f"  ターゲット客層: {target}")
    print(f"  メッセージ内容:\n{message}")
    print("="*50 + "\n")
    
    # Store inquiry in the database under demo_requests to keep leads visible in admin panel
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO demo_requests (name, email, business_name, industry, phone, tone, strengths, target, message, status) 
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (name, email, company or "未入力", industry or "未入力", phone, tone, strengths, target, message, "Inquiry Received")
    )
    conn.commit()
    conn.close()
    
    return RedirectResponse(url="/?contact_success=true", status_code=303)

# ----------------- AUTH ROUTING -----------------

@app.post("/register")
async def register(response: Response, email: str = Form(...), password: str = Form(...), name: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)",
            (email, password, name)
        )
        conn.commit()
        # Get registered user id
        cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
        user_row = cursor.fetchone()
        user_id = user_row["id"]
    except sqlite3.IntegrityError:
        conn.close()
        return RedirectResponse(url="/?reg_error=email_exists", status_code=303)
    conn.close()
    
    response = RedirectResponse(url="/mypage", status_code=303)
    response.set_cookie(key="user_id", value=str(user_id))
    return response

@app.post("/login")
async def login(response: Response, email: str = Form(...), password: str = Form(...)):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ? AND password_hash = ?", (email, password))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return RedirectResponse(url="/?login_error=invalid_credentials", status_code=303)
    
    dest = "/admin" if user["role"] == "admin" else "/mypage"
    response = RedirectResponse(url=dest, status_code=303)
    response.set_cookie(key="user_id", value=str(user["id"]))
    return response

@app.get("/logout")
async def logout(response: Response):
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("user_id")
    return response

# ----------------- MYPAGE ROUTING -----------------

@app.get("/mypage", response_class=HTMLResponse)
async def read_mypage(request: Request, user_id: str = Cookie(None)):
    current_user = get_current_user(user_id)
    if not current_user:
        return RedirectResponse(url="/", status_code=303)
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM websites WHERE user_id = ?", (current_user["id"],))
    website = cursor.fetchone()
    
    chats = []
    if website:
        cursor.execute("SELECT * FROM chat_requests WHERE website_id = ? ORDER BY created_at ASC", (website["id"],))
        chats = cursor.fetchall()
        
    conn.close()
    return templates.TemplateResponse(request, "mypage.html", {
        "user": current_user,
        "website": website,
        "chats": chats
    })

# ----------------- STRIPE SIMULATOR & PAYMENTS -----------------

@app.post("/create-checkout-session")
async def create_checkout_session(user_id: str = Cookie(None)):
    current_user = get_current_user(user_id)
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Fallback to simulated checkout if key is placeholder
    if config.get("stripe_api_key") == "sk_test_placeholder":
        # Simulate payment success directly
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET subscription_status = 'active' WHERE id = ?", (current_user["id"],))
        conn.commit()
        conn.close()
        return RedirectResponse(url="/mypage?payment=simulated", status_code=303)

    # Actual Stripe integration
    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'jpy',
                    'product_data': {
                        'name': 'L\'Anse Creative ホームページ制作・運用保守パッケージ',
                    },
                    'unit_amount': 2980, # 2,980 JPY
                    'recurring': {
                        'interval': 'month',
                    },
                },
                'quantity': 1,
            }],
            mode='subscription',
            success_url=config.get("app_url") + "/mypage?payment=success",
            cancel_url=config.get("app_url") + "/mypage?payment=cancelled",
            metadata={"user_id": str(current_user["id"])}
        )
        return RedirectResponse(url=checkout_session.url, status_code=303)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, config.get("stripe_webhook_secret")
        )
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    # Handle events
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        user_id = session.get("metadata", {}).get("user_id")
        sub_id = session.get("subscription")
        cust_id = session.get("customer")
        
        if user_id:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE users SET stripe_subscription_id = ?, stripe_customer_id = ?, subscription_status = 'active' WHERE id = ?",
                (sub_id, cust_id, int(user_id))
            )
            conn.commit()
            conn.close()
            
    elif event['type'] == 'invoice.payment_failed':
        invoice = event['data']['object']
        cust_id = invoice.get("customer")
        
        if cust_id:
            conn = get_db()
            cursor = conn.cursor()
            # If payment fails, mark user subscription as past_due
            cursor.execute(
                "UPDATE users SET subscription_status = 'past_due' WHERE stripe_customer_id = ?",
                (cust_id,)
            )
            # Also suspend any website
            cursor.execute(
                "UPDATE websites SET published_status = 'staging' WHERE user_id IN (SELECT id FROM users WHERE stripe_customer_id = ?)",
                (cust_id,)
            )
            conn.commit()
            conn.close()
            
    return JSONResponse(status_code=200, content={"status": "success"})

# ----------------- AI WEBSITE GENERATOR -----------------

MOCK_CONTENT = {
    "restaurant": {
        "hero_title": "本物の味を、アットホームな空間で。",
        "hero_desc": "厳選された地元食材を使用し、シェフ特製の本格イタリアンをお届けします。記念日やお祝い、気軽なランチにも最適です。",
        "about_title": "当店について",
        "about_text": "私たちは徳島で長年愛されている小さなレストランです。朝獲れの新鮮な海の幸と、地元の契約農家から届く有機野菜にこだわり、素材本来の旨味を引き出した一皿を心を込めてご提供しています。家族団欒、友人との食事、大切な日のディナーまで、どんなシーンでも温かくお迎えします。",
        "services": [
            {"name": "本格イタリアンランチ", "desc": "パスタ、メイン、デザート、コーヒーまで楽しめる大満足のランチコースです。"},
            {"name": "ディナーアラカルト & コース", "desc": "厳選ワインと相性抜群の肉料理、パスタ、前菜をご用意しています。"}
        ],
        "pricing": [
            {"name": "パスタランチコース", "price": "1,980円"},
            {"name": "シェフのおすすめディナーコース", "price": "4,980円"},
            {"name": "アニバーサリー特別ペアコース", "price": "12,000円"}
        ],
        "seo_meta_desc": "徳島で本格イタリアンを楽しむなら当店へ。新鮮な魚介と有機野菜にこだわったコースをご用意。記念日・ランチ・ディナーに最適。"
    },
    "salon": {
        "hero_title": "あなただけの、輝く美しさを引き出す場所。",
        "hero_desc": "一人ひとりの髪質や個性に寄り添い、丁寧なカウンセリングと確かな技術で、理想のスタイルを形にします。極上のヘッドスパも大人気。",
        "about_title": "当サロンについて",
        "about_text": "都会の喧騒を離れ、心からリラックスできるプライベートサロンです。髪と頭皮への負担を最小限に抑えたオーガニックカラー剤を使用し、ダメージケアとスタイル維持を両立。髪に関するお悩みは何でもお気軽にご相談ください。",
        "services": [
            {"name": "デザインカット & カラー", "desc": "髪質・骨格・トレンドに合わせた似合わせカットと、艶やかなオーガニックカラーのセット。"},
            {"name": "極上アロマヘッドスパ", "desc": "アロマの香りに包まれながら、頭皮の汚れを落とし疲れをしっかりとほぐす癒しの時間。"}
        ],
        "pricing": [
            {"name": "デザインカット", "price": "4,500円"},
            {"name": "カット ＋ オーガニックカラー", "price": "9,800円"},
            {"name": "カット ＋ カラー ＋ ヘッドスパ", "price": "13,500円"}
        ],
        "seo_meta_desc": "徳島のプライベートヘアサロン。髪に優しいオーガニック製品を使用し、あなたらしい似合わせスタイルをデザインします。ヘッドスパも完備。"
    },
    "default": {
        "hero_title": "プロの技術で、毎日の暮らしに安心を。",
        "hero_desc": "地域密着・迅速丁寧な対応を心がけています。小さな修理から大掛かりなリフォーム、各種お困りごとの解決までお気軽にお任せください。",
        "about_title": "私たちについて",
        "about_text": "「親切・丁寧・誠実」をモットーに、地域の皆様の快適な生活をサポートしております。確かな技術を持ったプロの職人が、一点一点迅速かつ丁寧に対応します。見積もりは無料ですので、お困りの際は何でもお声がけください。",
        "services": [
            {"name": "出張修理・メンテナンス", "desc": "突然のトラブルや機材の故障、調整作業までフットワーク軽く対応します。"},
            {"name": "生前整理・片付け代行", "desc": "不用品の仕分けや重たい家電の搬出など、お家のスッキリ片付けをサポート。"}
        ],
        "pricing": [
            {"name": "簡易見積もり・相談", "price": "完全無料"},
            {"name": "基本出張作業（1時間）", "price": "5,000円〜"},
            {"name": "まるごと片付け代行プラン", "price": "個別お見積もり"}
        ],
        "seo_meta_desc": "地域密着のお困りごと解決・出張サポート店。プロの技術で出張修理や整理片付け、住宅メンテナンスに迅速・親切丁寧に対応します。"
    }
}

@app.post("/generate-site")
async def generate_site(user_id: str = Cookie(None), business_name: str = Form(...), industry: str = Form(...), strengths: str = Form(...), target: str = Form(...), tone: str = Form(...), model_type: str = Form("flash")):
    current_user = get_current_user(user_id)
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Store questionnaire inputs
    inputs = {
        "strengths": strengths,
        "target": target,
        "tone": tone
    }
    inputs_json = json.dumps(inputs, ensure_ascii=False)
    
    conn = get_db()
    cursor = conn.cursor()
    
    # Run Gemini AI Generation or Fallback to mock
    generated_content = None
    if config.get("gemini_api_key") == "AIzaSy_placeholder":
        # Simulate local mock hydration based on industry
        ind_key = "restaurant" if "飲食" in industry or "カフェ" in industry or "レストラン" in industry else ("salon" if "美容" in industry or "ヘア" in industry or "エステ" in industry else "default")
        generated_content = json.dumps(MOCK_CONTENT[ind_key], ensure_ascii=False)
    else:
        # Prompt build
        model_name = "gemini-1.5-pro-latest" if model_type == "pro" else "gemini-1.5-flash-latest"
        prompt = f"""
        あなたはプロのWebディレクター兼コピーライターです。
        以下の店舗・サービスプロフィールを元に、最高に売れるコーポレート/ショップサイトの日本語テキストコンテンツを作成してください。

        【プロフィール情報】
        - 屋号/店舗名: {business_name}
        - 業種/ビジネスモデル: {industry}
        - 店舗の強み: {strengths}
        - ターゲット層: {target}
        - トーン＆マナー: {tone}

        必ず、以下のJSONスキーマに従った有効なJSONオブジェクトのみを出力してください。追加の説明やマークダウン修飾（```jsonのようなブロック表記も含む）は一切排除し、純粋なJSON文字列だけを返してください。

        【出力フォーマット（JSONスキーマ）】
        {{
            "hero_title": "キャッチコピー（顧客の目を引く20文字〜30文字程度）",
            "hero_desc": "ヒーロー文言（事業の説明や価値、40文字〜80文字）",
            "about_title": "紹介セクションのタイトル",
            "about_text": "紹介文（事業のこだわり、背景、強み、100文字〜200文字）",
            "services": [
                {{"name": "サービス・品目名 1", "desc": "説明 40文字程度"}},
                {{"name": "サービス・品目名 2", "desc": "説明 40文字程度"}}
            ],
            "pricing": [
                {{"name": "プラン・品目料金 1", "price": "価格（例：〇〇円）"}},
                {{"name": "プラン・品目料金 2", "price": "価格（例：〇〇円）"}},
                {{"name": "プラン・品目料金 3", "price": "価格（例：〇〇円）"}}
            ],
            "seo_meta_desc": "SEO用のディスクリプション（100文字程度、キーワードを自然に含める）"
        }}
        """
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            # Remove markdown backticks if returned
            resp_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            # Validate JSON
            json.loads(resp_text)
            generated_content = resp_text
        except Exception as e:
            # Fallback on Gemini error
            ind_key = "restaurant" if "飲食" in industry or "カフェ" in industry or "レストラン" in industry else ("salon" if "美容" in industry or "ヘア" in industry or "エステ" in industry else "default")
            generated_content = json.dumps(MOCK_CONTENT[ind_key], ensure_ascii=False)
            
    # Save or Update SQLite
    cursor.execute("SELECT id FROM websites WHERE user_id = ?", (current_user["id"],))
    site_exists = cursor.fetchone()
    
    if site_exists:
        cursor.execute(
            "UPDATE websites SET business_name = ?, industry = ?, inputs_json = ?, generated_content_json = ? WHERE user_id = ?",
            (business_name, industry, inputs_json, generated_content, current_user["id"])
        )
    else:
        cursor.execute(
            "INSERT INTO websites (user_id, business_name, industry, inputs_json, generated_content_json) VALUES (?, ?, ?, ?, ?)",
            (current_user["id"], business_name, industry, inputs_json, generated_content)
        )
        
    conn.commit()
    conn.close()
    return RedirectResponse(url="/mypage?generate=success", status_code=303)

@app.post("/request-revision")
async def request_revision(user_id: str = Cookie(None), message: str = Form(...)):
    current_user = get_current_user(user_id)
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, business_name, industry, generated_content_json FROM websites WHERE user_id = ?", (current_user["id"],))
    website = cursor.fetchone()
    if not website:
        conn.close()
        raise HTTPException(status_code=400, detail="Website not found")
        
    # 1. Save user chat message
    cursor.execute(
        "INSERT INTO chat_requests (website_id, sender, message) VALUES (?, ?, ?)",
        (website["id"], "client", message)
    )
    
    # 2. Run Gemini Flash for quick copywriting adjustment
    bot_reply = "修正のご要望を承りました！制作チームがレイアウトとテキストを調整し、即時プレビューに反映しました。"
    new_content_json = None
    
    if config.get("gemini_api_key") == "AIzaSy_placeholder":
        # Simulate simple revision text change
        try:
            curr_data = json.loads(website["generated_content_json"])
            curr_data["hero_title"] = curr_data["hero_title"] + " (修正反映済)"
            new_content_json = json.dumps(curr_data, ensure_ascii=False)
            bot_reply = "ご要望に基づき、コピーを微調整いたしました！プレビューをご確認ください。"
        except Exception:
            pass
    else:
        # Prompt for Flash correction
        prompt = f"""
        あなたはWebサイトのテキスト修正を行うアシスタントです。
        既存のサイトコンテンツ(JSON形式)に対して、お客様から届いた修正指示を反映し、アップデートしたコンテンツを生成してください。

        【既存のコンテンツ】
        {website["generated_content_json"]}

        【修正指示】
        "{message}"

        必ず、既存と同じJSONスキーマ(hero_title, hero_desc, about_title, about_text, services, pricing, seo_meta_desc)に適合した有効なJSONオブジェクトのみを出力してください。追加の説明や```jsonのようなマークダウンブロックは一切排除してください。
        """
        try:
            model = genai.GenerativeModel("gemini-1.5-flash-latest")
            response = model.generate_content(prompt)
            resp_text = response.text.strip().replace("```json", "").replace("```", "").strip()
            # Validate
            json.loads(resp_text)
            new_content_json = resp_text
            bot_reply = "ご指摘通りに文章の一部を更新いたしました！"
        except Exception as e:
            pass
            
    if new_content_json:
        cursor.execute(
            "UPDATE websites SET generated_content_json = ? WHERE id = ?",
            (new_content_json, website["id"])
        )
        
    # Save bot chat message reply
    cursor.execute(
        "INSERT INTO chat_requests (website_id, sender, message) VALUES (?, ?, ?)",
        (website["id"], "ai", bot_reply)
    )
    
    conn.commit()
    conn.close()
    return RedirectResponse(url="/mypage?revision=success", status_code=303)

@app.post("/request-domain")
async def request_domain(user_id: str = Cookie(None), domain: str = Form(...)):
    current_user = get_current_user(user_id)
    if not current_user:
        raise HTTPException(status_code=401, detail="Unauthorized")
        
    conn = get_db()
    conn.execute(
        "UPDATE websites SET domain_requested = ?, domain_status = 'pending' WHERE user_id = ?",
        (domain, current_user["id"])
    )
    conn.commit()
    conn.close()
    return RedirectResponse(url="/mypage?domain=requested", status_code=303)

# ----------------- ADMIN CONTROLLER ROUTING -----------------

@app.get("/admin", response_class=HTMLResponse)
async def read_admin(request: Request, user_id: str = Cookie(None)):
    current_user = get_current_user(user_id)
    if not current_user or current_user["role"] != "admin":
        return RedirectResponse(url="/", status_code=303)
        
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("SELECT u.*, w.business_name, w.domain_requested, w.domain_status, w.published_status FROM users u LEFT JOIN websites w ON u.id = w.user_id WHERE u.role != 'admin'")
    users = cursor.fetchall()
    
    cursor.execute("SELECT * FROM demo_requests ORDER BY created_at DESC")
    demo_requests = cursor.fetchall()
    
    conn.close()
    return templates.TemplateResponse(request, "admin.html", {
        "users": users,
        "demo_requests": demo_requests
    })

@app.post("/admin/approve-domain/{user_id}")
async def approve_domain(user_id: int):
    conn = get_db()
    conn.execute("UPDATE websites SET domain_status = 'active', published_status = 'live' WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return RedirectResponse(url="/admin?domain=approved", status_code=303)

@app.post("/admin/toggle-status/{user_id}")
async def toggle_status(user_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT published_status FROM websites WHERE user_id = ?", (user_id,))
    site = cursor.fetchone()
    if site:
        new_status = "live" if site["published_status"] == "staging" else "staging"
        cursor.execute("UPDATE websites SET published_status = ? WHERE user_id = ?", (new_status, user_id))
        conn.commit()
    conn.close()
    return RedirectResponse(url="/admin?status=toggled", status_code=303)

# ----------------- STAGING / DYNAMIC CUSTOMER SITE VIEWER -----------------

@app.get("/site/{website_id}", response_class=HTMLResponse)
async def view_customer_site(request: Request, website_id: int):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM websites WHERE id = ?", (website_id,))
    website = cursor.fetchone()
    conn.close()
    
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
        
    try:
        content = json.loads(website["generated_content_json"])
    except Exception:
        raise HTTPException(status_code=500, detail="Corrupted website content")
        
    return templates.TemplateResponse(request, "site_template.html", {
        "website": website,
        "content": content
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=config.get("port", 8080), reload=True)
