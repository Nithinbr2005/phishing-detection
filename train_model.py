import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix, accuracy_score, f1_score
from sklearn.calibration import CalibratedClassifierCV
import joblib
import json

TARGET_FALSE_POSITIVE_RATE = 0.05  # target max false positive rate when tuning threshold

# Load the data
print("Loading data...")
mail_data = pd.read_csv('mail_data.csv')
mail_data = mail_data.where((pd.notnull(mail_data)), '')

# Verify data
print(f"Total emails: {len(mail_data)}")
print(f"Spam count: {(mail_data['Category'] == 'spam').sum()}")
print(f"Legitimate count: {(mail_data['Category'] == 'legitimate').sum()}")

# Prepare the data
X = mail_data['Message']
# Label mapping: legitimate=0, spam=1
y = mail_data['Category'].map({'legitimate': 0, 'spam': 1})

# Split data for validation and testing
X_train_full, X_test, y_train_full, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
# Further split training into train + validation for calibration/threshold tuning
X_train, X_val, y_train, y_val = train_test_split(
    X_train_full, y_train_full, test_size=0.2, random_state=42, stratify=y_train_full
)
print(f"\nTraining set: {len(X_train)} samples")
print(f"Test set: {len(X_test)} samples")

# Create and fit the vectorizer
print("\nTraining vectorizer...")
vectorizer = TfidfVectorizer(
    max_features=5000,        # Limit to top 5000 features
    min_df=2,                 # Ignore terms that appear in less than 2 documents
    max_df=0.8,               # Ignore terms that appear in more than 80% of documents
    ngram_range=(1, 2),       # Use unigrams and bigrams
    lowercase=True,
    stop_words='english'
)
X_train_transformed = vectorizer.fit_transform(X_train)
X_test_transformed = vectorizer.transform(X_test)

print(f"Vectorizer created with {len(vectorizer.get_feature_names_out())} features")

# Train the model
print("\nTraining Logistic Regression model...")
base_model = LogisticRegression(
    max_iter=1000,
    random_state=42,
    class_weight='balanced'  # Handle class imbalance
)
base_model.fit(X_train_transformed, y_train)

# Calibrate probabilities using cross-validation calibration
print("Calibrating probabilities with CalibratedClassifierCV (cv=3)...")
calibrator = CalibratedClassifierCV(base_model, cv=3, method='sigmoid')
calibrator.fit(X_train_transformed, y_train)
model = calibrator  # use calibrated model for downstream predictions
X_val_transformed = vectorizer.transform(X_val)

# Evaluate the model
print("\n" + "="*50)
print("MODEL EVALUATION")
print("="*50)

y_pred_train = model.predict(X_train_transformed)
y_pred_test = model.predict(X_test_transformed)

print(f"\nTraining Accuracy: {accuracy_score(y_train, y_pred_train):.4f}")
print(f"Test Accuracy: {accuracy_score(y_test, y_pred_test):.4f}")

print("\nTest Set Classification Report:")
print(classification_report(y_test, y_pred_test, target_names=['Legitimate', 'Spam']))

print("\nConfusion Matrix (Test Set):")
print(confusion_matrix(y_test, y_pred_test))

# Tune decision threshold on validation set to control false positives
print("\nTuning decision threshold on validation set...")
X_val_full = X_val_transformed
val_probs = model.predict_proba(X_val_full)[:, 1]
best_thresh = 0.5
best_f1 = f1_score(y_val, (val_probs >= best_thresh).astype(int))
for t in np.linspace(0.1, 0.9, 81):
    preds = (val_probs >= t).astype(int)
    f1 = f1_score(y_val, preds)
    # compute false positive rate
    tn = ((y_val == 0) & (preds == 0)).sum()
    fp = ((y_val == 0) & (preds == 1)).sum()
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0
    # prefer thresholds that keep false positives below target, maximize F1
    if fpr <= TARGET_FALSE_POSITIVE_RATE and f1 > best_f1:
        best_f1 = f1
        best_thresh = t

print(f"Selected threshold: {best_thresh:.2f} (validation F1={best_f1:.3f})")

# Save the model and vectorizer
print("\nSaving model, vectorizer and threshold...")
joblib.dump(model, 'ai_detection_model.pkl')
joblib.dump(vectorizer, 'vectorizer.pkl')
with open('threshold.json', 'w') as fh:
    json.dump({"threshold": float(best_thresh)}, fh)

print("✓ Model trained and saved successfully!")
print("\nLabel Mapping:")
print("  0 = Legitimate email")
print("  1 = Spam email") 