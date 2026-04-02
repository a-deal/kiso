# Metric Catalog

## Tier 1: Foundation

### Blood Pressure (Weight: 8)
- **Metric**: Systolic / Diastolic
- **Source**: Home cuff (Omron) or clinic
- **Scoring**: NHANES continuous percentiles; fallback AHA/ACC thresholds
- **Evidence**: Each 20 mmHg >115 SBP doubles CVD mortality
- **Cost to acquire**: $40 one-time (Omron cuff)

### Lipid Panel + ApoB (Weight: 8)
- **Metric**: ApoB preferred, LDL-C fallback; also HDL-C, Triglycerides
- **Source**: Fasting blood draw
- **Scoring**: NHANES percentiles; ApoB via ESC/EAS guidelines
- **Evidence**: ApoB outperforms LDL-C for risk prediction (Mendelian randomization)
- **Cost to acquire**: $30-50/yr

### Metabolic Panel (Weight: 8)
- **Metric**: Fasting insulin preferred, HbA1c fallback, glucose last resort
- **Source**: Fasting blood draw
- **Scoring**: NHANES percentiles
- **Evidence**: Fasting insulin catches insulin resistance 10-15 years before diagnosis
- **Cost to acquire**: $40-60/yr

### Family History (Weight: 6)
- **Metric**: Binary — collected or not
- **Source**: Self-report (10 min conversation)
- **Evidence**: Parental CVD before age 60 approximately doubles risk
- **Cost to acquire**: Free

### Sleep Regularity (Weight: 5)
- **Metric**: Standard deviation of bedtime in minutes
- **Source**: Wearable sleep tracking
- **Scoring**: Windred et al. (UK Biobank) cutoffs
- **Evidence**: Regularity predicts mortality independent of duration
- **Cost to acquire**: Free with any wearable

### Daily Steps (Weight: 4)
- **Metric**: Average steps per day
- **Source**: Phone or wearable
- **Scoring**: Tudor-Locke classification
- **Evidence**: Each +1,000 steps/day = ~15% lower all-cause mortality (Paluch et al.)
- **Cost to acquire**: Free with phone

### Resting Heart Rate (Weight: 4)
- **Metric**: 30-day average RHR
- **Source**: Wearable
- **Scoring**: NHANES percentiles
- **Evidence**: RHR >75 associated with doubled mortality vs <60 (Copenhagen Heart Study)
- **Cost to acquire**: Free with wearable

### Waist Circumference (Weight: 5)
- **Metric**: Waist circumference in inches
- **Source**: Tape measure at navel level
- **Scoring**: NHANES percentiles
- **Evidence**: Better visceral fat proxy than BMI; M >40in / F >35in = high risk
- **Cost to acquire**: $3 tape measure

### Medication List (Weight: 4)
- **Metric**: Binary — collected or not
- **Source**: Self-report
- **Evidence**: Essential context for interpreting all other data
- **Cost to acquire**: Free

### Lp(a) (Weight: 8)
- **Metric**: Lipoprotein(a) in nmol/L
- **Source**: Blood draw (once in lifetime — genetically fixed)
- **Scoring**: Copenhagen GPS published data
- **Evidence**: 20% of people have elevated Lp(a), invisible on standard panels, 2-3x CVD risk
- **Cost to acquire**: $30 once

## Tier 2: Enhanced

### VO2 Max (Weight: 6)
- **Evidence**: Strongest modifiable predictor of all-cause mortality (JACC 2018)
- **Scoring**: ACSM fitness classifications by age/sex

### HRV — RMSSD (Weight: 2)
- **Evidence**: Parasympathetic tone proxy; 7-day rolling avg most reliable
- **Scoring**: Manual cutoff tables by age/sex (no NHANES data)

### hs-CRP (Weight: 3)
- **Evidence**: Adds CVD risk stratification beyond lipids (JUPITER trial)
- **Scoring**: NHANES percentiles

### Liver Enzymes — GGT/ALT (Weight: 2)
- **Evidence**: GGT independently predicts CV mortality and diabetes
- **Scoring**: NHANES percentiles

### CBC — Hemoglobin (Weight: 2)
- **Evidence**: Safety net screening; RDW predicts all-cause mortality
- **Scoring**: NHANES percentiles

### Thyroid — TSH (Weight: 2)
- **Evidence**: 12% lifetime prevalence, highly treatable
- **Scoring**: Bidirectional (both high and low are bad); NHANES + guideline cutoffs

### Vitamin D + Ferritin (Weight: 3)
- **Evidence**: 42% of US adults vitamin D deficient; cheap to fix
- **Scoring**: NHANES percentiles + Endocrine Society guidelines

### Weight Trends (Weight: 2)
- **Evidence**: Progressive drift is the signal, not absolute weight
- **Scoring**: Binary — tracked or not

### PHQ-9 (Weight: 2)
- **Evidence**: Depression independently raises CVD risk 80%
- **Scoring**: Binary — screened or not (actual PHQ-9 is 0-27 scale)

### Zone 2 Cardio (Weight: 2)
- **Evidence**: 150-300 min/week = largest all-cause mortality reduction
- **Scoring**: Binary — tracked or not
