import java.io.ByteArrayInputStream;
import java.io.ObjectInputStream;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.ResultSet;
import java.sql.Statement;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import nutrient.calculator.DailyRecords;
import nutrient.calculator.TotalAverageDaily;

/**
 * Narrow bridge for historical Java ObjectOutputStream BLOBs.
 *
 * The bridge never writes SQLite. It emits one JSON object per BLOB so Python
 * can normalize the recovered evidence without re-serializing or mutating it.
 */
public class LegacyBlobDecoder {
    private static String quote(String input) {
        StringBuilder out = new StringBuilder("\"");
        for (char c : input.toCharArray()) {
            switch (c) {
                case '\\': out.append("\\\\"); break;
                case '"': out.append("\\\""); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                default:
                    if (c < 0x20) out.append(String.format("\\u%04x", (int)c));
                    else out.append(c);
            }
        }
        return out.append('"').toString();
    }

    private static String json(Object value) {
        if (value == null) return "null";
        if (value instanceof String) return quote((String)value);
        if (value instanceof Number || value instanceof Boolean) return value.toString();
        if (value instanceof Map<?, ?>) {
            StringBuilder out = new StringBuilder("{");
            boolean first = true;
            for (Map.Entry<?, ?> entry : ((Map<?, ?>)value).entrySet()) {
                if (!first) out.append(',');
                first = false;
                out.append(quote(String.valueOf(entry.getKey()))).append(':').append(json(entry.getValue()));
            }
            return out.append('}').toString();
        }
        if (value instanceof Iterable<?>) {
            StringBuilder out = new StringBuilder("[");
            boolean first = true;
            for (Object item : (Iterable<?>)value) {
                if (!first) out.append(',');
                first = false;
                out.append(json(item));
            }
            return out.append(']').toString();
        }
        return quote(value.toString());
    }

    private static Object read(byte[] blob) throws Exception {
        try (ObjectInputStream input = new ObjectInputStream(new ByteArrayInputStream(blob))) {
            return input.readObject();
        }
    }

    private static Map<String, Object> daily(DailyRecords d) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("person_id", d.getPersonID());
        out.put("person_name", d.getPersonName());
        out.put("day_eaten", d.getDayEaten());
        out.put("food_id", d.getFoodID());
        out.put("food_name", d.getFoodName());
        out.put("eaten_food_weight", d.getEatenFoodWeight());
        out.put("real_food_weight", d.getRealFoodWeight());
        out.put("recommended_food_weight", d.getRecommendedFoodWeight());
        out.put("total_eaten_basic_components", d.getTotalEatenBasicComponents());
        out.put("total_eaten_vitamins", d.getTotalEatenVitamins());
        out.put("total_eaten_minerals", d.getTotalEatenMinerals());
        out.put("total_eaten_poly_fats", d.getTotalEatenPolyFats());
        out.put("total_eaten_other_nutrients", d.getTotalEatenOtherNutrients());
        out.put("total_eaten_toxicants", d.getTotalEatenToxicants());
        out.put("recommended_macro", d.getRecommendedMacro());
        out.put("recommended_vitamins", d.getRecommendedVits());
        out.put("recommended_minerals", d.getRecommendedMins());
        return out;
    }

    private static Map<String, Object> total(TotalAverageDaily t) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("total_macro", t.getTotalMacro());
        out.put("total_vitamins", t.getTotalVitamins());
        out.put("total_minerals", t.getTotalMinerals());
        out.put("total_toxicants", t.getTotalToxicants());
        out.put("average_macro", t.getAverageMacro());
        out.put("average_vitamins", t.getAverageVitamins());
        out.put("average_minerals", t.getAverageMinerals());
        out.put("average_toxicants", t.getAverageToxicants());
        out.put("total_recommended_macro", t.getTotalRecommendedMacroNutrients());
        out.put("total_recommended_vitamins", t.getTotalRecommendedVitamins());
        out.put("total_recommended_minerals", t.getTotalRecommendedMinerals());
        out.put("average_recommended_macro", t.getAverageRecommendedMacroNutrients());
        out.put("average_recommended_vitamins", t.getAverageRecommendedVitamins());
        out.put("average_recommended_minerals", t.getAverageRecommendedMinerals());
        return out;
    }

    private static void emitFailure(String kind, int rowId, String column, Exception error) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("kind", kind);
        out.put("row_id", rowId);
        out.put("column", column);
        out.put("status", "decode_error");
        out.put("error_type", error.getClass().getName());
        out.put("error", error.getMessage());
        System.out.println(json(out));
    }

    private static void decodeDaily(Connection con) throws Exception {
        String[] days = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
        try (Statement st = con.createStatement(); ResultSet rs = st.executeQuery("SELECT * FROM Daily_Records ORDER BY id")) {
            while (rs.next()) {
                for (String day : days) {
                    byte[] blob = rs.getBytes(day);
                    if (blob == null) continue;
                    try {
                        Object obj = read(blob);
                        if (!(obj instanceof DailyRecords)) throw new IllegalStateException("Unexpected class " + obj.getClass().getName());
                        Map<String, Object> out = new LinkedHashMap<>();
                        out.put("kind", "daily_record");
                        out.put("row_id", rs.getInt("id"));
                        out.put("db_person_id", rs.getInt("Person_ID"));
                        out.put("column", day);
                        out.put("status", "decoded");
                        out.put("object", daily((DailyRecords)obj));
                        System.out.println(json(out));
                    } catch (Exception error) {
                        emitFailure("daily_record", rs.getInt("id"), day, error);
                    }
                }
            }
        }
    }

    private static void decodeTotals(Connection con) throws Exception {
        try (Statement st = con.createStatement(); ResultSet rs = st.executeQuery("SELECT * FROM Total_Average ORDER BY id")) {
            while (rs.next()) {
                byte[] blob = rs.getBytes("Total_Average_Daily");
                if (blob == null) continue;
                try {
                    Object obj = read(blob);
                    if (!(obj instanceof TotalAverageDaily)) throw new IllegalStateException("Unexpected class " + obj.getClass().getName());
                    Map<String, Object> out = new LinkedHashMap<>();
                    out.put("kind", "total_average");
                    out.put("row_id", rs.getInt("id"));
                    out.put("db_person_id", rs.getInt("Person_ID"));
                    out.put("column", "Total_Average_Daily");
                    out.put("status", "decoded");
                    out.put("object", total((TotalAverageDaily)obj));
                    System.out.println(json(out));
                } catch (Exception error) {
                    emitFailure("total_average", rs.getInt("id"), "Total_Average_Daily", error);
                }
            }
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) throw new IllegalArgumentException("Usage: LegacyBlobDecoder <Nutrients.db>");
        Class.forName("org.sqlite.JDBC");
        String url = "jdbc:sqlite:file:" + args[0] + "?mode=ro";
        try (Connection con = DriverManager.getConnection(url)) {
            try (Statement guard = con.createStatement()) {
                guard.execute("PRAGMA query_only = ON");
            }
            decodeDaily(con);
            decodeTotals(con);
        }
    }
}
