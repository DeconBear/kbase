package dev.klynx.kbase;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final int PICK_CONTEXT_FILE = 2001;
    private static final int MAX_CONTEXT_BYTES = 80 * 1024;
    private static final String PREFS = "kbase_mobile";
    private static final String PREF_ENDPOINT = "endpoint";
    private static final String PREF_MODEL = "model";
    private static final String PREF_API_KEY = "api_key";
    private static final String DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions";
    private static final String DEFAULT_MODEL = "gpt-4o-mini";

    private EditText endpointInput;
    private EditText modelInput;
    private EditText apiKeyInput;
    private EditText promptInput;
    private TextView contextLabel;
    private TextView transcriptView;
    private ProgressBar progressBar;
    private Button sendButton;
    private String contextText = "";
    private String contextName = "";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        loadSettings();
        appendSystem("Configure an OpenAI-compatible API endpoint, API key, and model. This app calls the cloud API directly and does not require the KBase desktop server.");
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(12), dp(10), dp(12), dp(10));
        root.setBackgroundColor(Color.rgb(248, 250, 252));

        TextView title = new TextView(this);
        title.setText("KBase Mobile");
        title.setTextSize(22);
        title.setTextColor(Color.rgb(15, 23, 42));
        title.setGravity(Gravity.CENTER_VERTICAL);
        title.setPadding(0, 0, 0, dp(8));
        root.addView(title, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));

        endpointInput = new EditText(this);
        endpointInput.setSingleLine(true);
        endpointInput.setHint(DEFAULT_ENDPOINT);
        endpointInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        root.addView(endpointInput, inputParams());

        modelInput = new EditText(this);
        modelInput.setSingleLine(true);
        modelInput.setHint(DEFAULT_MODEL);
        root.addView(modelInput, inputParams());

        apiKeyInput = new EditText(this);
        apiKeyInput.setSingleLine(true);
        apiKeyInput.setHint("API key");
        apiKeyInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        root.addView(apiKeyInput, inputParams());

        LinearLayout settingsRow = new LinearLayout(this);
        settingsRow.setOrientation(LinearLayout.HORIZONTAL);
        settingsRow.setGravity(Gravity.CENTER_VERTICAL);
        Button saveButton = button("Save");
        Button pickFileButton = button("Context file");
        Button clearContextButton = button("Clear");
        settingsRow.addView(saveButton, rowButtonParams());
        settingsRow.addView(pickFileButton, rowButtonParams());
        settingsRow.addView(clearContextButton, rowButtonParams());
        root.addView(settingsRow);

        contextLabel = new TextView(this);
        contextLabel.setText("No context file loaded");
        contextLabel.setTextColor(Color.rgb(100, 116, 139));
        contextLabel.setTextSize(12);
        contextLabel.setPadding(0, dp(4), 0, dp(8));
        root.addView(contextLabel);

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setVisibility(View.GONE);
        root.addView(progressBar, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(3)));

        ScrollView scrollView = new ScrollView(this);
        transcriptView = new TextView(this);
        transcriptView.setTextSize(14);
        transcriptView.setTextColor(Color.rgb(15, 23, 42));
        transcriptView.setLineSpacing(0, 1.15f);
        transcriptView.setPadding(dp(12), dp(12), dp(12), dp(12));
        scrollView.addView(transcriptView);
        root.addView(scrollView, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        LinearLayout promptRow = new LinearLayout(this);
        promptRow.setOrientation(LinearLayout.HORIZONTAL);
        promptRow.setGravity(Gravity.BOTTOM);
        promptInput = new EditText(this);
        promptInput.setMinLines(1);
        promptInput.setMaxLines(4);
        promptInput.setHint("Ask KBase...");
        promptInput.setImeOptions(EditorInfo.IME_ACTION_SEND);
        promptInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        sendButton = button("Send");
        promptRow.addView(promptInput, new LinearLayout.LayoutParams(0, dp(58), 1));
        promptRow.addView(sendButton, new LinearLayout.LayoutParams(dp(82), dp(58)));
        root.addView(promptRow);

        setContentView(root);

        saveButton.setOnClickListener(v -> {
            saveSettings();
            appendSystem("Settings saved on this device.");
        });
        pickFileButton.setOnClickListener(v -> pickContextFile());
        clearContextButton.setOnClickListener(v -> clearContext());
        sendButton.setOnClickListener(v -> sendPrompt());
        promptInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEND) {
                sendPrompt();
                return true;
            }
            return false;
        });
    }

    private LinearLayout.LayoutParams inputParams() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, dp(46));
        params.setMargins(0, 0, 0, dp(8));
        return params;
    }

    private LinearLayout.LayoutParams rowButtonParams() {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(0, dp(44), 1);
        params.setMargins(0, 0, dp(8), 0);
        return params;
    }

    private Button button(String text) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        return button;
    }

    private void loadSettings() {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        endpointInput.setText(prefs.getString(PREF_ENDPOINT, DEFAULT_ENDPOINT));
        modelInput.setText(prefs.getString(PREF_MODEL, DEFAULT_MODEL));
        apiKeyInput.setText(prefs.getString(PREF_API_KEY, ""));
    }

    private void saveSettings() {
        getSharedPreferences(PREFS, MODE_PRIVATE)
                .edit()
                .putString(PREF_ENDPOINT, endpoint())
                .putString(PREF_MODEL, model())
                .putString(PREF_API_KEY, apiKeyInput.getText().toString().trim())
                .apply();
    }

    private String endpoint() {
        String endpoint = endpointInput.getText().toString().trim();
        return endpoint.length() == 0 ? DEFAULT_ENDPOINT : endpoint;
    }

    private String model() {
        String model = modelInput.getText().toString().trim();
        return model.length() == 0 ? DEFAULT_MODEL : model;
    }

    private void pickContextFile() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{
                "text/plain",
                "text/markdown",
                "application/json",
                "application/xml",
                "text/*"
        });
        startActivityForResult(intent, PICK_CONTEXT_FILE);
    }

    private void clearContext() {
        contextText = "";
        contextName = "";
        contextLabel.setText("No context file loaded");
        appendSystem("Context cleared.");
    }

    private void sendPrompt() {
        String prompt = promptInput.getText().toString().trim();
        String apiKey = apiKeyInput.getText().toString().trim();
        if (prompt.length() == 0) {
            return;
        }
        if (apiKey.length() == 0) {
            appendSystem("API key is required.");
            return;
        }
        saveSettings();
        promptInput.setText("");
        appendMessage("You", prompt);
        setBusy(true);

        String endpoint = endpoint();
        String model = model();
        String context = contextText;
        new Thread(() -> {
            try {
                String answer = callChatCompletions(endpoint, apiKey, model, prompt, context);
                runOnUiThread(() -> appendMessage("Assistant", answer));
            } catch (Exception e) {
                runOnUiThread(() -> appendSystem("Request failed: " + e.getMessage()));
            } finally {
                runOnUiThread(() -> setBusy(false));
            }
        }).start();
    }

    private String callChatCompletions(
            String endpoint,
            String apiKey,
            String model,
            String prompt,
            String context) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("model", model);
        payload.put("temperature", 0.2);

        JSONArray messages = new JSONArray();
        messages.put(new JSONObject()
                .put("role", "system")
                .put("content", "You are KBase Mobile, a concise research and knowledge-base assistant."));
        if (context != null && context.length() > 0) {
            messages.put(new JSONObject()
                    .put("role", "user")
                    .put("content", "Use this local context when it is relevant:\n\n" + context));
        }
        messages.put(new JSONObject().put("role", "user").put("content", prompt));
        payload.put("messages", messages);

        HttpURLConnection conn = (HttpURLConnection) new URL(endpoint).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(30000);
        conn.setReadTimeout(120000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        conn.setRequestProperty("Authorization", "Bearer " + apiKey);

        byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
        try (OutputStream out = conn.getOutputStream()) {
            out.write(body);
        }

        int status = conn.getResponseCode();
        InputStream input = status >= 200 && status < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readStream(input, 512 * 1024);
        if (status < 200 || status >= 300) {
            throw new IllegalStateException("HTTP " + status + ": " + response);
        }

        JSONObject json = new JSONObject(response);
        JSONArray choices = json.optJSONArray("choices");
        if (choices == null || choices.length() == 0) {
            throw new IllegalStateException("No choices returned");
        }
        JSONObject message = choices.getJSONObject(0).optJSONObject("message");
        if (message == null) {
            throw new IllegalStateException("No message returned");
        }
        String content = message.optString("content", "").trim();
        return content.length() == 0 ? "(empty response)" : content;
    }

    private String readStream(InputStream input, int maxBytes) throws Exception {
        if (input == null) {
            return "";
        }
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[4096];
        int total = 0;
        int read;
        while ((read = input.read(chunk)) != -1) {
            int allowed = Math.min(read, maxBytes - total);
            if (allowed > 0) {
                buffer.write(chunk, 0, allowed);
                total += allowed;
            }
            if (total >= maxBytes) {
                break;
            }
        }
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    private void setBusy(boolean busy) {
        progressBar.setVisibility(busy ? View.VISIBLE : View.GONE);
        sendButton.setEnabled(!busy);
    }

    private void appendSystem(String text) {
        appendMessage("KBase", text);
    }

    private void appendMessage(String role, String text) {
        transcriptView.append(role + "\n" + text + "\n\n");
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_CONTEXT_FILE || resultCode != RESULT_OK || data == null || data.getData() == null) {
            return;
        }
        Uri uri = data.getData();
        try (InputStream input = getContentResolver().openInputStream(uri)) {
            contextText = readStream(input, MAX_CONTEXT_BYTES);
            contextName = uri.getLastPathSegment() == null ? "selected file" : uri.getLastPathSegment();
            contextLabel.setText("Context loaded: " + contextName + " (" + contextText.length() + " chars)");
            appendSystem("Loaded context file: " + contextName);
        } catch (Exception e) {
            appendSystem("Could not read context file: " + e.getMessage());
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
