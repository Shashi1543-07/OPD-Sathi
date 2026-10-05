# Synthetic evaluation results

> Draft rule and routing content has not received clinical or native-speaker review. These synthetic cases are generated from the same phrase vocabulary as the rules, so scores are implementation checks and do not estimate real-world clinical performance.

## Red flags

```json
{
  "dev": {
    "scripts": 350,
    "tp": 205,
    "fp": 0,
    "fn": 0,
    "tn": 158,
    "precision": 1.0,
    "recall": 1.0,
    "must_catch": {
      "caught": 0,
      "total": 0
    },
    "negation": {
      "correct": 88,
      "total": 88
    },
    "per_rule": {
      "RF_ALLERGY_SEVERE": {
        "tp": 6,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_BLEEDING_HEAVY": {
        "tp": 10,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_BREATH_SEVERE": {
        "tp": 12,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHEST_PAIN": {
        "tp": 46,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHEST_PAIN_ASSOC": {
        "tp": 11,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHILD_FEVER": {
        "tp": 11,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHILD_FEVER_ASSOC": {
        "tp": 7,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_DS_TILE": {
        "tp": 6,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_HIGH_FEVER": {
        "tp": 10,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_POISON_BITE": {
        "tp": 11,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_PREGNANCY_DANGER": {
        "tp": 15,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_SEIZURE_FAINT": {
        "tp": 14,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_SELF_HARM": {
        "tp": 14,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_SEVERE_PAIN": {
        "tp": 16,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_STROKE_SIGNS": {
        "tp": 6,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_TRAUMA_MAJOR": {
        "tp": 10,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      }
    }
  },
  "test": {
    "scripts": 150,
    "tp": 84,
    "fp": 0,
    "fn": 0,
    "tn": 70,
    "precision": 1.0,
    "recall": 1.0,
    "must_catch": {
      "caught": 60,
      "total": 60
    },
    "negation": {
      "correct": 50,
      "total": 50
    },
    "per_rule": {
      "RF_ALLERGY_SEVERE": {
        "tp": 5,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_BLEEDING_HEAVY": {
        "tp": 5,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_BREATH_SEVERE": {
        "tp": 6,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHEST_PAIN": {
        "tp": 16,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHEST_PAIN_ASSOC": {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHILD_FEVER": {
        "tp": 5,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_CHILD_FEVER_ASSOC": {
        "tp": 2,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_DS_TILE": {
        "tp": 5,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_HIGH_FEVER": {
        "tp": 2,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_POISON_BITE": {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_PREGNANCY_DANGER": {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_SEIZURE_FAINT": {
        "tp": 8,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_SELF_HARM": {
        "tp": 4,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_SEVERE_PAIN": {
        "tp": 7,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_STROKE_SIGNS": {
        "tp": 7,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      },
      "RF_TRAUMA_MAJOR": {
        "tp": 3,
        "fp": 0,
        "fn": 0,
        "recall": 1.0,
        "precision": 1.0
      }
    }
  },
  "dataset_profile": {
    "scripts": 500,
    "languages": {
      "en": 200,
      "hi": 150,
      "hinglish": 50,
      "mr": 100
    },
    "splits": {
      "dev": 350,
      "test": 150
    },
    "test_kinds": {
      "benign": 30,
      "must": 60,
      "negation": 50,
      "positive": 10
    }
  },
  "misses": []
}
```

## Department routing

```json
{
  "dev": {
    "cases": 224,
    "correct": 224,
    "top1_accuracy": 1.0,
    "confusion_matrix": {
      "DERM": {
        "DERM": 28
      },
      "ENT": {
        "ENT": 28
      },
      "GENMED": {
        "GENMED": 24
      },
      "NURSE_TRIAGE": {
        "NURSE_TRIAGE": 28
      },
      "OBG": {
        "OBG": 28
      },
      "OPHTH": {
        "OPHTH": 28
      },
      "ORTHO": {
        "ORTHO": 24
      },
      "PAED": {
        "PAED": 8
      },
      "SURG": {
        "SURG": 28
      }
    },
    "misses": []
  },
  "test": {
    "cases": 96,
    "correct": 96,
    "top1_accuracy": 1.0,
    "confusion_matrix": {
      "DERM": {
        "DERM": 12
      },
      "ENT": {
        "ENT": 12
      },
      "GENMED": {
        "GENMED": 12
      },
      "NURSE_TRIAGE": {
        "NURSE_TRIAGE": 12
      },
      "OBG": {
        "OBG": 12
      },
      "OPHTH": {
        "OPHTH": 12
      },
      "ORTHO": {
        "ORTHO": 12
      },
      "SURG": {
        "SURG": 12
      }
    },
    "misses": []
  }
}
```
