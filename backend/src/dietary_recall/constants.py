"""Legacy schema and compatibility constants.

These names deliberately mirror the Java/SQLite application.  Scientific
corrections belong in a future ``validated_research`` package and must not be
silently introduced here.
"""

from __future__ import annotations

DAY_COLUMNS = (
    "Sunday",
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
)

COMPOSITION_TABLES = (
    "Basic_Components",
    "Vitamins",
    "Minerals",
    "Poly_Fats",
    "Other_Nutrients",
    "Toxicants",
)

DRI_TABLES = ("Basic_Components_DRI", "Vitamins_DRI", "Minerals_DRI")

# In the Java code the first value read after Food_ID/Food_Name is a table
# marker. FoodAnalysisProcess injects a 100 g reference weight before the
# actual nutrient columns, so the arrays below represent indices 1..n.
BASIC_FIELDS = (
    "Calories_kcal",
    "Protein_g",
    "Carbohydrates_g",
    "Dietary_Fibre_g",
    "Soluble_Fibre_g",
    "Total_Sugars_g",
    "Monosaccharides_g",
    "Disaccharides_g",
    "Other_Carbs_g",
    "Fat_g",
    "Saturated_Fat_g",
    "Mono_Fat_g",
    "Poly_Fat_g",
    "Trans_Fatty_Acid_mg",
    "Cholesterol_mg",
    "Water_g",
)

VITAMIN_FIELDS = (
    "Vitamin_A_IU_IU",
    "Vitamin_A_RAE_RAE",
    "Carotenoid_RE_RE",
    "Retinol_RE_RE",
    "BetaCarotene_mcg",
    "Vitamin_B1_mg",
    "Vitamin_B2_mg",
    "Vitamin_B3_mg",
    "Vitamin_B6_mg",
    "Vitamin_B12_mcg",
    "Vitamin_C_mg",
    "Vitamin_D_IU_IU",
    "Vitamin_D_mcg_mcg",
    "Vitamin_E_mg",
    "Niacin_mg",
    "Biotin_mcg",
    "Folate_mcg",
    "Vitamin_K_mcg",
    "Panthotenic_Acid_mg",
)

MINERAL_FIELDS = (
    "Calcium_mg",
    "Chromium_mcg",
    "Copper_mg",
    "Fluoride_mg",
    "Iodine_mcg",
    "Iron_mg",
    "Magnesium_mg",
    "Manganese_mg",
    "Molybdenum_mcg",
    "Phosphorus_mg",
    "Potassium_mg",
    "Selenium_mcg",
    "Sodium_mg",
    "Zinc_mg",
)

POLY_FAT_FIELDS = ("Omega_3_Fatty_Acid_g", "Omega_6_Fatty_Acid_g")
OTHER_FIELDS = ("Alcohol_g", "Caffeine_mg")
TOXICANT_FIELDS = ("Cadmium_mcg", "Lead_mcg")

TABLE_FIELDS = {
    "Basic_Components": BASIC_FIELDS,
    "Vitamins": VITAMIN_FIELDS,
    "Minerals": MINERAL_FIELDS,
    "Poly_Fats": POLY_FAT_FIELDS,
    "Other_Nutrients": OTHER_FIELDS,
    "Toxicants": TOXICANT_FIELDS,
}

DRI_FIELDS = {
    "Basic_Components_DRI": (
        "Carbohydrate_g",
        "Total_Fibre_g",
        "Fat_g",
        "Protein_g",
    ),
    "Vitamins_DRI": (
        "Vitamin_A_mcg",
        "Vitamin_C_mg",
        "Vitamin_D_mcg",
        "Vitamin_E_mg",
        "Vitamin_K_mcg",
        "Thiamin_mg",
        "Riboflavin_mg",
        "Niacin_mg",
        "Vitamin_B6_mg",
        "Folate_mcg",
        "Vitamin_B12_mcg",
        "Pantothenic_Acid_mg",
        "Biotin_mcg",
        "Choline_mg",
    ),
    "Minerals_DRI": (
        "Calcium_mg",
        "Chromium_mcg",
        "Copper_mcg",
        "Flouride_mg",
        "Iodine_mcg",
        "Iron_mg",
        "Magnesium_mg",
        "Manganese_mg",
        "Molybdenum_mcg",
        "Phosphorus_mg",
        "Selenium_mcg",
        "Zinc_mg",
    ),
}

ACTIVITY_MULTIPLIERS = {
    1: 0.75,
    2: 0.90,
    3: 1.00,
    4: 1.25,
    5: 1.50,
    "sedentary": 0.75,
    "lightly_active": 0.90,
    "moderate": 1.00,
    "very_active": 1.25,
    "extreme": 1.50,
}

JAVA_NULL_AS_ZERO_TABLES = set(TABLE_FIELDS)
REFERENCE_FOOD_WEIGHT_G = 100.0

