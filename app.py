from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import joblib
import logging
import os
import re
from html import unescape

from file_extractors import extract_text_from_file

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="."), name="static")

# Global variables for model and vectorizer
model = None
vectorizer = None
THRESHOLD = 0.5
ALLOWLIST = set()
URL_OVERRIDE_THRESHOLD = 0.45

def load_models():
    global model, vectorizer, THRESHOLD
    try:
        logger.info("Loading ML models...")
        logger.info(f"Current working directory: {os.getcwd()}")
        logger.info(f"Model file exists: {os.path.exists('ai_detection_model.pkl')}")
        logger.info(f"Vectorizer file exists: {os.path.exists('vectorizer.pkl')}")
        
        model = joblib.load("ai_detection_model.pkl")
        vectorizer = joblib.load("vectorizer.pkl")
        # load threshold if available
        try:
            import json
            if os.path.exists('threshold.json'):
                with open('threshold.json','r') as fh:
                    data = json.load(fh)
                    THRESHOLD = float(data.get('threshold', 0.5))
        except Exception:
            # ignore threshold load failures
            pass
        # load allowlist if available
        try:
            import json
            if os.path.exists('allowlist.json'):
                with open('allowlist.json','r') as fh:
                    data = json.load(fh)
                    if isinstance(data, list):
                        ALLOWLIST.clear()
                        for d in data:
                            ALLOWLIST.add(d.lower())
        except Exception:
            pass
        
        # Test the model and vectorizer
        test_text = "This is a test email"
        X_test = vectorizer.transform([test_text])
        prediction = model.predict(X_test)
        logger.info(f"Model test prediction: {prediction}")
        
        logger.info("ML models loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Error loading models: {str(e)}")
        return False


def extract_urls(text: str):
    # simple URL regex (http, https, www, or bare domains)
    url_pattern = re.compile(r"(https?://[^\s'\"<>]+|www\.[^\s'\"<>]+|[a-z0-9\-]+\.[a-z]{2,}(?:/[^\s'\"<>]*)?)", re.IGNORECASE)
    return url_pattern.findall(text or "")


def compute_url_score(urls: list) -> float:
    if not urls:
        return 0.0
    score = 0.0
    from urllib.parse import urlparse
    suspicious_tlds = {"xyz","top","click","party","gq","work","tk","zip","review","date"}
    suspicious_paths = {"verify","confirm","secure","login","update","billing","account","verify-email"}
    for u in urls:
        u_raw = u
        u = u.lower()
        parsed = urlparse(u if u.startswith('http') else ('http://' + u))
        host = parsed.netloc or parsed.path
        # strip port if present
        host = host.split(':')[0]

        # skip scoring for allowlisted hosts (exact or suffix match)
        for trusted in ALLOWLIST:
            if host == trusted or host.endswith('.' + trusted):
                # treat as safe: do not add to score
                host = None
                break
        if not host:
            continue

        # IP address in hostname
        if re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", host):
            score += 0.9
        # credential-like @ in URL
        if "@" in u_raw:
            score += 0.8
        # suspicious TLD
        if '.' in host:
            tld = host.split('.')[-1]
            if tld in suspicious_tlds:
                score += 0.7
        # suspicious path keywords
        path_lower = parsed.path.lower()
        for kw in suspicious_paths:
            if kw in path_lower:
                score += 0.5
        # very long URL
        if len(u) > 120:
            score += 0.4
        # many subdomains
        subdomain_count = host.count('.')
        if subdomain_count >= 3:
            score += 0.25
    # additional boost for multiple URLs
    if len(urls) > 1:
        score += min(0.35 * (len(urls)-1), 0.6)
    # normalize to 0-1 (expected max roughly 2.5-3.0)
    return max(0.0, min(1.0, score / 2.5))


def clean_text(text: str) -> str:
    if not text:
        return ""
    # unescape HTML entities
    text = unescape(text)
    # remove simple HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # find and replace URLs with a token so vectorizer sees consistent token
    urls = extract_urls(text)
    if urls:
        for u in set(urls):
            text = text.replace(u, " __URL__ ")
    # normalize whitespace and lowercase
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text

# Load models on startup
if not load_models():
    raise HTTPException(status_code=500, detail="Error loading ML models")

@app.get("/")
async def read_root():
    return FileResponse('index.html')

@app.post("/predict")
async def predict(email: str = Form(...)):
    try:
        if model is None or vectorizer is None:
            if not load_models():
                raise HTTPException(status_code=500, detail="Error loading models")
            
        logger.info(f"Received prediction request for email: {email[:50]}...")
        cleaned = clean_text(email)
        X = vectorizer.transform([cleaned])
        prediction = model.predict(X)[0]
        probabilities = model.predict_proba(X)[0]
        # model probability of spam
        spam_confidence = float(probabilities[1])
        # simple URL-aware ensemble: if URLs exist, blend model prob with URL heuristic
        urls = extract_urls(email)
        url_score = compute_url_score(urls)
        if urls:
            final_confidence = 0.5 * spam_confidence + 0.5 * url_score
        else:
            final_confidence = spam_confidence
        # Rule-based override: if URL heuristic is strong, mark as spam immediately
        if url_score >= URL_OVERRIDE_THRESHOLD:
            is_spam = True
        else:
            is_spam = final_confidence >= THRESHOLD
        result = {"label": "spam" if is_spam else "legitimate", "confidence": round(final_confidence, 2)}
        logger.info(f"Prediction completed: {result}")
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"Error during prediction: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during prediction: {str(e)}")


@app.post("/predict/file")
async def predict_file(file: UploadFile = File(...)):
    """Analyze uploaded file (image, PDF, or text) for phishing."""
    try:
        if model is None or vectorizer is None:
            if not load_models():
                raise HTTPException(status_code=500, detail="Error loading models")

        content = await file.read()
        if len(content) == 0:
            raise HTTPException(status_code=400, detail="Empty file uploaded")

        # Limit file size (10 MB)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="File too large. Maximum size is 10 MB.")

        logger.info(f"Processing uploaded file: {file.filename} ({len(content)} bytes)")
        extracted_text = extract_text_from_file(content, file.filename or "file")

        if not extracted_text.strip():
            raise HTTPException(status_code=400, detail="No text could be extracted from the file.")

        logger.info(f"Extracted {len(extracted_text)} chars, running prediction...")
        cleaned = clean_text(extracted_text)
        X = vectorizer.transform([cleaned])
        probabilities = model.predict_proba(X)[0]
        spam_confidence = float(probabilities[1])

        # URL heuristics: extract original URLs from extracted_text (not cleaned)
        urls = extract_urls(extracted_text)
        url_score = compute_url_score(urls)

        # Combine model and URL heuristic — URL heuristic matters more when the content is short or URL-heavy
        length = len(extracted_text.strip())
        if length < 300 and urls:
            final_confidence = 0.4 * spam_confidence + 0.6 * url_score
        elif urls:
            final_confidence = 0.5 * spam_confidence + 0.5 * url_score
        else:
            final_confidence = spam_confidence

        # Rule-based override for file uploads as well
        if url_score >= URL_OVERRIDE_THRESHOLD:
            is_spam = True
        else:
            is_spam = final_confidence >= THRESHOLD
        result = {
            "label": "spam" if is_spam else "legitimate",
            "confidence": round(final_confidence, 2),
            "extracted_text": extracted_text[:500] + ("..." if len(extracted_text) > 500 else ""),
            "detected_urls": urls,
            "url_score": round(url_score, 2),
        }
        logger.info(f"File prediction completed: {result['label']} (final_confidence={result['confidence']})")
        return JSONResponse(content=result)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error during file prediction: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error during prediction: {str(e)}")


@app.get("/checking")
async def checking():
    return FileResponse('checking.html')


@app.get("/health")
async def health():
    """Return simple health information: model load status and tesseract path detection."""
    # Model status
    model_loaded = model is not None and vectorizer is not None

    # Determine tesseract path using same priority as in file_extractors.py
    tesseract_path = os.environ.get("TESSERACT_CMD") or os.environ.get("TESSERACT_PATH")
    if not tesseract_path:
        default_path = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        try:
            if os.path.exists(default_path):
                tesseract_path = default_path
        except Exception:
            tesseract_path = None

    info = {
        "model_loaded": bool(model_loaded),
        "tesseract_path": tesseract_path,
        "threshold": THRESHOLD,
            "allowlist": sorted(list(ALLOWLIST)),
        "url_override_threshold": URL_OVERRIDE_THRESHOLD,
    }
    return JSONResponse(content=info)
