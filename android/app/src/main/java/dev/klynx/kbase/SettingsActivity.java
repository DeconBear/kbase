package dev.klynx.kbase;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

public class SettingsActivity extends Activity {
    private static final String PREFS = "kbase_mobile";
    
    // Chat LLM
    private static final String PREF_ENDPOINT = "endpoint";
    private static final String PREF_MODEL = "model";
    private static final String PREF_API_KEY = "api_key";
    
    // Vision OCR
    private static final String PREF_VISION_TYPE = "vision_type";
    private static final String PREF_VISION_URL = "vision_url";
    private static final String PREF_VISION_MODEL = "vision_model";
    private static final String PREF_VISION_API_KEY = "vision_api_key";

    // PDF Parser
    private static final String PREF_PARSER_ENDPOINT = "parser_endpoint";
    private static final String PREF_PARSER_API_KEY = "parser_api_key";

    // Defaults
    private static final String DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions";
    private static final String DEFAULT_MODEL = "gpt-4o-mini";
    private static final String DEFAULT_VISION_URL = "https://api.openai.com/v1";
    private static final String DEFAULT_PARSER_ENDPOINT = "https://your-cloud-parser.com/api/parse";

    private EditText endpointInput;
    private EditText modelInput;
    private EditText apiKeyInput;
    
    private Spinner visionTypeSpinner;
    private EditText visionUrlInput;
    private EditText visionModelInput;
    private EditText visionApiKeyInput;

    private EditText parserEndpointInput;
    private EditText parserApiKeyInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(Color.rgb(248, 250, 252));

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(16), dp(24), dp(16), dp(40));
        scroll.addView(root);

        // Header
        TextView title = new TextView(this);
        title.setText("Settings");
        title.setTextSize(26);
        title.setTextColor(Color.rgb(15, 23, 42));
        title.setPadding(0, 0, 0, dp(20));
        root.addView(title);

        // 1. Chat LLM Card
        root.addView(createCardTitle("Chat LLM Configuration"));
        LinearLayout chatCard = createCard();
        endpointInput = addInputField(chatCard, "API URL", DEFAULT_ENDPOINT, InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        modelInput = addInputField(chatCard, "Model Name", DEFAULT_MODEL, InputType.TYPE_CLASS_TEXT);
        apiKeyInput = addInputField(chatCard, "API Key", "sk-...", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(chatCard);

        // 2. Vision OCR Card
        root.addView(createCardTitle("Vision OCR Configuration"));
        LinearLayout visionCard = createCard();
        
        TextView typeLabel = new TextView(this);
        typeLabel.setText("API Type");
        typeLabel.setTextSize(13);
        typeLabel.setTextColor(Color.rgb(100, 116, 139));
        typeLabel.setPadding(dp(4), dp(8), 0, dp(4));
        visionCard.addView(typeLabel);
        
        visionTypeSpinner = new Spinner(this);
        String[] types = new String[]{"OpenAI Compatible", "Anthropic", "Minimax"};
        ArrayAdapter<String> adapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, types);
        visionTypeSpinner.setAdapter(adapter);
        visionTypeSpinner.setBackground(createInputBackground());
        visionCard.addView(visionTypeSpinner, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)));

        visionUrlInput = addInputField(visionCard, "API Base URL", DEFAULT_VISION_URL, InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        visionModelInput = addInputField(visionCard, "Model Name", "gpt-4o", InputType.TYPE_CLASS_TEXT);
        visionApiKeyInput = addInputField(visionCard, "API Key", "sk-...", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(visionCard);

        // 3. PDF Parser Card
        root.addView(createCardTitle("Cloud PDF Parser"));
        LinearLayout parserCard = createCard();
        parserEndpointInput = addInputField(parserCard, "Parser Endpoint", DEFAULT_PARSER_ENDPOINT, InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        parserApiKeyInput = addInputField(parserCard, "Parser API Key", "sk-...", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(parserCard);

        // Action Buttons
        LinearLayout buttonRow = new LinearLayout(this);
        buttonRow.setOrientation(LinearLayout.HORIZONTAL);
        buttonRow.setGravity(Gravity.END);
        buttonRow.setPadding(0, dp(16), 0, 0);

        Button cancelBtn = new Button(this);
        cancelBtn.setText("Cancel");
        cancelBtn.setBackgroundColor(Color.TRANSPARENT);
        cancelBtn.setTextColor(Color.rgb(100, 116, 139));
        cancelBtn.setAllCaps(false);
        cancelBtn.setOnClickListener(v -> finish());
        
        Button saveBtn = new Button(this);
        saveBtn.setText("Save");
        saveBtn.setTextColor(Color.WHITE);
        saveBtn.setAllCaps(false);
        GradientDrawable btnBg = new GradientDrawable();
        btnBg.setColor(Color.rgb(16, 185, 129)); // Emerald 500
        btnBg.setCornerRadius(dp(8));
        saveBtn.setBackground(btnBg);
        saveBtn.setOnClickListener(v -> {
            saveSettings();
            Toast.makeText(this, "Settings Saved", Toast.LENGTH_SHORT).show();
            finish();
        });

        buttonRow.addView(cancelBtn, new LinearLayout.LayoutParams(dp(100), dp(48)));
        LinearLayout.LayoutParams saveParams = new LinearLayout.LayoutParams(dp(100), dp(48));
        saveParams.setMargins(dp(8), 0, 0, 0);
        buttonRow.addView(saveBtn, saveParams);
        root.addView(buttonRow);

        setContentView(scroll);
        loadSettings();
    }

    private TextView createCardTitle(String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(14);
        tv.setTextColor(Color.rgb(71, 85, 105));
        tv.setPadding(dp(4), dp(16), 0, dp(8));
        return tv;
    }

    private LinearLayout createCard() {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(16), dp(8), dp(16), dp(16));
        
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.WHITE);
        bg.setCornerRadius(dp(12));
        card.setBackground(bg);
        card.setElevation(dp(2));
        
        return card;
    }

    private EditText addInputField(LinearLayout parent, String labelText, String hint, int inputType) {
        TextView label = new TextView(this);
        label.setText(labelText);
        label.setTextSize(13);
        label.setTextColor(Color.rgb(100, 116, 139));
        label.setPadding(dp(4), dp(12), 0, dp(4));
        parent.addView(label);

        EditText input = new EditText(this);
        input.setSingleLine(true);
        input.setHint(hint);
        input.setHintTextColor(Color.rgb(203, 213, 225));
        input.setTextColor(Color.rgb(30, 41, 59));
        input.setTextSize(15);
        input.setInputType(inputType);
        input.setBackground(createInputBackground());
        input.setPadding(dp(12), dp(12), dp(12), dp(12));
        
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        parent.addView(input, params);
        
        return input;
    }

    private GradientDrawable createInputBackground() {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.rgb(248, 250, 252));
        bg.setStroke(dp(1), Color.rgb(226, 232, 240));
        bg.setCornerRadius(dp(8));
        return bg;
    }

    private void loadSettings() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        endpointInput.setText(prefs.getString(PREF_ENDPOINT, DEFAULT_ENDPOINT));
        modelInput.setText(prefs.getString(PREF_MODEL, DEFAULT_MODEL));
        apiKeyInput.setText(prefs.getString(PREF_API_KEY, ""));

        visionTypeSpinner.setSelection(prefs.getInt(PREF_VISION_TYPE, 0));
        visionUrlInput.setText(prefs.getString(PREF_VISION_URL, DEFAULT_VISION_URL));
        visionModelInput.setText(prefs.getString(PREF_VISION_MODEL, ""));
        visionApiKeyInput.setText(prefs.getString(PREF_VISION_API_KEY, ""));

        parserEndpointInput.setText(prefs.getString(PREF_PARSER_ENDPOINT, DEFAULT_PARSER_ENDPOINT));
        parserApiKeyInput.setText(prefs.getString(PREF_PARSER_API_KEY, ""));
    }

    private void saveSettings() {
        String endpoint = endpointInput.getText().toString().trim();
        if (endpoint.length() == 0) endpoint = DEFAULT_ENDPOINT;
        
        String model = modelInput.getText().toString().trim();
        if (model.length() == 0) model = DEFAULT_MODEL;
        
        String parserEndpoint = parserEndpointInput.getText().toString().trim();
        if (parserEndpoint.length() == 0) parserEndpoint = DEFAULT_PARSER_ENDPOINT;

        getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putString(PREF_ENDPOINT, endpoint)
                .putString(PREF_MODEL, model)
                .putString(PREF_API_KEY, apiKeyInput.getText().toString().trim())
                .putInt(PREF_VISION_TYPE, visionTypeSpinner.getSelectedItemPosition())
                .putString(PREF_VISION_URL, visionUrlInput.getText().toString().trim())
                .putString(PREF_VISION_MODEL, visionModelInput.getText().toString().trim())
                .putString(PREF_VISION_API_KEY, visionApiKeyInput.getText().toString().trim())
                .putString(PREF_PARSER_ENDPOINT, parserEndpoint)
                .putString(PREF_PARSER_API_KEY, parserApiKeyInput.getText().toString().trim())
                .apply();
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
