package dev.klynx.kbase;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.graphics.pdf.PdfRenderer;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.view.inputmethod.EditorInfo;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.Toast;

import androidx.annotation.NonNull;
import androidx.recyclerview.widget.LinearLayoutManager;
import androidx.recyclerview.widget.RecyclerView;
import androidx.viewpager2.widget.ViewPager2;

import com.github.chrisbanes.photoview.PhotoView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

public class MainActivity extends Activity {
    private static final int PICK_CONTEXT_FILE = 2001;
    private static final String PREFS = "kbase_mobile";
    private static final String PREF_ENDPOINT = "endpoint";
    private static final String PREF_MODEL = "model";
    private static final String PREF_API_KEY = "api_key";
    private static final String PREF_PARSER_ENDPOINT = "parser_endpoint";
    private static final String PREF_PARSER_API_KEY = "parser_api_key";
    private static final String DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions";
    private static final String DEFAULT_MODEL = "gpt-4o-mini";
    private static final String DEFAULT_PARSER_ENDPOINT = "https://your-cloud-parser.com";

    private EditText promptInput;
    private TextView contextLabel;
    private TextView readerView;
    private LinearLayout transcriptContainer;
    private ScrollView transcriptScroll;
    private ProgressBar progressBar;
    private Button sendButton;
    private ViewPager2 viewPager;
    private RecyclerView pdfRecycler;

    private String contextText = "";
    private String contextName = "";
    private PdfRenderer pdfRenderer;
    private ParcelFileDescriptor pdfDescriptor;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildUi();
        appendSystem("KBase Mobile ready. Swipe left/right in the upper area to switch views.");
        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    private void handleIntent(Intent intent) {
        if (intent != null && (Intent.ACTION_VIEW.equals(intent.getAction()) || Intent.ACTION_SEND.equals(intent.getAction()))) {
            Uri uri = intent.getData();
            if (uri == null && intent.hasExtra(Intent.EXTRA_STREAM)) {
                uri = intent.getParcelableExtra(Intent.EXTRA_STREAM);
            }
            if (uri != null) {
                loadUri(uri);
            }
        }
    }

    private void buildUi() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(241, 245, 249)); // slate-100

        // Top App Bar
        LinearLayout topBar = new LinearLayout(this);
        topBar.setOrientation(LinearLayout.HORIZONTAL);
        topBar.setGravity(Gravity.CENTER_VERTICAL);
        topBar.setPadding(dp(16), dp(12), dp(16), dp(12));
        topBar.setBackgroundColor(Color.WHITE);
        topBar.setElevation(dp(4));

        TextView title = new TextView(this);
        title.setText("KBase");
        title.setTextSize(20);
        title.getPaint().setFakeBoldText(true);
        title.setTextColor(Color.rgb(15, 23, 42));
        topBar.addView(title, new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        Button pickFileButton = createIconButton("📄 Open");
        pickFileButton.setOnClickListener(v -> pickContextFile());
        topBar.addView(pickFileButton);

        Button settingsButton = createIconButton("⚙");
        settingsButton.setOnClickListener(v -> startActivity(new Intent(this, SettingsActivity.class)));
        LinearLayout.LayoutParams setParams = new LinearLayout.LayoutParams(dp(40), dp(36));
        setParams.setMargins(dp(8), 0, 0, 0);
        topBar.addView(settingsButton, setParams);

        root.addView(topBar);

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setVisibility(View.GONE);
        root.addView(progressBar, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(3)));

        // Split Area (Reader & Chat)
        LinearLayout splitArea = new LinearLayout(this);
        splitArea.setOrientation(LinearLayout.VERTICAL);

        // Header for Reader
        contextLabel = new TextView(this);
        contextLabel.setText("No document loaded");
        contextLabel.setTextColor(Color.rgb(100, 116, 139));
        contextLabel.setTextSize(12);
        contextLabel.setPadding(dp(16), dp(8), dp(16), dp(4));
        splitArea.addView(contextLabel);

        // Reader View (Top Half using ViewPager2)
        viewPager = new ViewPager2(this);
        viewPager.setBackgroundColor(Color.WHITE);
        
        GradientDrawable vpBg = new GradientDrawable();
        vpBg.setColor(Color.WHITE);
        vpBg.setCornerRadius(dp(12));
        
        LinearLayout vpContainer = new LinearLayout(this);
        vpContainer.setPadding(dp(16), dp(0), dp(16), dp(8));
        
        LinearLayout vpInner = new LinearLayout(this);
        vpInner.setBackground(vpBg);
        vpInner.setElevation(dp(2));
        vpInner.addView(viewPager, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT));
        
        vpContainer.addView(vpInner, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.MATCH_PARENT));

        viewPager.setAdapter(new PagerAdapter());
        
        LinearLayout.LayoutParams readerParams = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1.3f);
        splitArea.addView(vpContainer, readerParams);

        // Chat Transcript (Bottom Half)
        transcriptScroll = new ScrollView(this);
        transcriptContainer = new LinearLayout(this);
        transcriptContainer.setOrientation(LinearLayout.VERTICAL);
        transcriptContainer.setPadding(dp(16), dp(8), dp(16), dp(16));
        transcriptScroll.addView(transcriptContainer);
        
        splitArea.addView(transcriptScroll, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        root.addView(splitArea, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        // Bottom Input Row (Floating Dock style)
        LinearLayout dock = new LinearLayout(this);
        dock.setOrientation(LinearLayout.VERTICAL);
        dock.setBackgroundColor(Color.WHITE);
        dock.setElevation(dp(8));
        dock.setPadding(dp(16), dp(12), dp(16), dp(12));

        LinearLayout promptRow = new LinearLayout(this);
        promptRow.setOrientation(LinearLayout.HORIZONTAL);
        promptRow.setGravity(Gravity.BOTTOM);

        promptInput = new EditText(this);
        promptInput.setMinLines(1);
        promptInput.setMaxLines(4);
        promptInput.setHint("Ask KBase...");
        promptInput.setTextSize(15);
        promptInput.setTextColor(Color.rgb(30, 41, 59));
        promptInput.setPadding(dp(16), dp(12), dp(16), dp(12));
        
        GradientDrawable inputBg = new GradientDrawable();
        inputBg.setColor(Color.rgb(241, 245, 249));
        inputBg.setCornerRadius(dp(24));
        promptInput.setBackground(inputBg);
        
        promptInput.setImeOptions(EditorInfo.IME_ACTION_SEND);
        promptInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        
        sendButton = new Button(this);
        sendButton.setText("↑");
        sendButton.setTextSize(20);
        sendButton.setTextColor(Color.WHITE);
        GradientDrawable sendBg = new GradientDrawable();
        sendBg.setColor(Color.rgb(16, 185, 129));
        sendBg.setCornerRadius(dp(24));
        sendButton.setBackground(sendBg);

        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        promptRow.addView(promptInput, inputParams);
        
        LinearLayout.LayoutParams sendParams = new LinearLayout.LayoutParams(dp(48), dp(48));
        sendParams.setMargins(dp(8), 0, 0, 0);
        promptRow.addView(sendButton, sendParams);
        
        dock.addView(promptRow);
        root.addView(dock);

        setContentView(root);

        sendButton.setOnClickListener(v -> sendPrompt());
    }

    private Button createIconButton(String text) {
        Button btn = new Button(this);
        btn.setText(text);
        btn.setAllCaps(false);
        btn.setTextSize(14);
        btn.setTextColor(Color.rgb(71, 85, 105));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.rgb(241, 245, 249));
        bg.setCornerRadius(dp(8));
        btn.setBackground(bg);
        btn.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, dp(36)));
        return btn;
    }

    private class PagerAdapter extends RecyclerView.Adapter<RecyclerView.ViewHolder> {
        @NonNull
        @Override
        public RecyclerView.ViewHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            if (viewType == 0) {
                pdfRecycler = new RecyclerView(MainActivity.this);
                pdfRecycler.setLayoutManager(new LinearLayoutManager(MainActivity.this));
                pdfRecycler.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                return new RecyclerView.ViewHolder(pdfRecycler) {};
            } else {
                ScrollView scroll = new ScrollView(MainActivity.this);
                scroll.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
                readerView = new TextView(MainActivity.this);
                readerView.setTextSize(14);
                readerView.setTextColor(Color.rgb(30, 41, 59));
                readerView.setPadding(dp(16), dp(16), dp(16), dp(16));
                readerView.setLineSpacing(0, 1.2f);
                readerView.setText("No document loaded. Open a file to read.");
                if (contextText.length() > 0) readerView.setText(contextText);
                scroll.addView(readerView);
                return new RecyclerView.ViewHolder(scroll) {};
            }
        }

        @Override
        public void onBindViewHolder(@NonNull RecyclerView.ViewHolder holder, int position) {
            if (position == 0 && pdfRecycler != null && pdfRenderer != null) {
                pdfRecycler.setAdapter(new PdfPageAdapter(pdfRenderer));
            }
        }

        @Override
        public int getItemCount() { return 2; }

        @Override
        public int getItemViewType(int position) { return position; }
    }

    private class PdfPageAdapter extends RecyclerView.Adapter<PdfPageAdapter.PageHolder> {
        private final PdfRenderer renderer;
        PdfPageAdapter(PdfRenderer renderer) { this.renderer = renderer; }

        @NonNull
        @Override
        public PageHolder onCreateViewHolder(@NonNull ViewGroup parent, int viewType) {
            PhotoView pv = new PhotoView(MainActivity.this);
            pv.setLayoutParams(new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
            pv.setAdjustViewBounds(true);
            pv.setBackgroundColor(Color.rgb(241, 245, 249)); // light grey gap between pages
            pv.setPadding(0, dp(4), 0, dp(4));
            return new PageHolder(pv);
        }

        @Override
        public void onBindViewHolder(@NonNull PageHolder holder, int position) {
            try {
                PdfRenderer.Page page = renderer.openPage(position);
                int w = getResources().getDisplayMetrics().widthPixels;
                // Render at higher resolution for zooming
                int renderW = w * 2;
                int renderH = (int) (renderW * ((float) page.getHeight() / page.getWidth()));
                Bitmap bitmap = Bitmap.createBitmap(renderW, renderH, Bitmap.Config.ARGB_8888);
                
                bitmap.eraseColor(Color.WHITE);
                page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY);
                holder.pv.setImageBitmap(bitmap);
                page.close();
            } catch (Exception e) {
                e.printStackTrace();
            }
        }

        @Override
        public int getItemCount() { return renderer.getPageCount(); }

        class PageHolder extends RecyclerView.ViewHolder {
            PhotoView pv;
            PageHolder(PhotoView pv) { super(pv); this.pv = pv; }
        }
    }

    private void appendMessage(String role, String text) {
        boolean isUser = role.equalsIgnoreCase("You");
        
        LinearLayout wrapper = new LinearLayout(this);
        wrapper.setOrientation(LinearLayout.HORIZONTAL);
        wrapper.setLayoutParams(new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        wrapper.setGravity(isUser ? Gravity.END : Gravity.START);
        wrapper.setPadding(0, dp(4), 0, dp(8));

        TextView bubble = new TextView(this);
        bubble.setText(text);
        bubble.setTextSize(14);
        bubble.setPadding(dp(12), dp(10), dp(12), dp(10));
        bubble.setTextColor(isUser ? Color.WHITE : Color.rgb(30, 41, 59));
        
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(isUser ? Color.rgb(16, 185, 129) : Color.WHITE);
        bg.setCornerRadius(dp(16));
        if (isUser) {
            bg.setCornerRadii(new float[]{dp(16), dp(16), dp(4), dp(16), dp(16), dp(16), dp(16), dp(16)});
        } else {
            bg.setCornerRadii(new float[]{dp(4), dp(16), dp(16), dp(16), dp(16), dp(16), dp(16), dp(16)});
            bg.setStroke(dp(1), Color.rgb(226, 232, 240));
        }
        bubble.setBackground(bg);
        bubble.setLineSpacing(0, 1.2f);
        bubble.setTextIsSelectable(true);

        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        params.weight = 0;
        // Max width to prevent overly wide bubbles
        bubble.setMaxWidth((int)(getResources().getDisplayMetrics().widthPixels * 0.85));
        
        wrapper.addView(bubble, params);
        transcriptContainer.addView(wrapper);

        // Scroll to bottom
        transcriptScroll.post(() -> transcriptScroll.fullScroll(View.FOCUS_DOWN));
    }

    private void appendSystem(String text) {
        TextView tv = new TextView(this);
        tv.setText(text);
        tv.setTextSize(12);
        tv.setTextColor(Color.rgb(148, 163, 184));
        tv.setGravity(Gravity.CENTER);
        tv.setPadding(0, dp(8), 0, dp(8));
        transcriptContainer.addView(tv);
        transcriptScroll.post(() -> transcriptScroll.fullScroll(View.FOCUS_DOWN));
    }

    private void pickContextFile() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/pdf", "text/*"});
        startActivityForResult(intent, PICK_CONTEXT_FILE);
    }

    private void closePdf() {
        try {
            if (pdfRenderer != null) { pdfRenderer.close(); pdfRenderer = null; }
            if (pdfDescriptor != null) { pdfDescriptor.close(); pdfDescriptor = null; }
        } catch (Exception e) { e.printStackTrace(); }
    }

    private void loadUri(Uri uri) {
        closePdf();
        contextName = uri.getLastPathSegment() == null ? "document" : uri.getLastPathSegment();
        contextLabel.setText("Loaded: " + contextName);
        setBusy(true);

        new Thread(() -> {
            try {
                File cacheFile = new File(getCacheDir(), "temp_doc");
                try (InputStream is = getContentResolver().openInputStream(uri);
                     FileOutputStream fos = new FileOutputStream(cacheFile)) {
                    byte[] buf = new byte[8192];
                    int len;
                    while ((len = is.read(buf)) > 0) { fos.write(buf, 0, len); }
                }

                String mimeType = getContentResolver().getType(uri);
                boolean isPdf = (mimeType != null && mimeType.contains("pdf")) || contextName.toLowerCase().endsWith(".pdf");

                if (isPdf) {
                    pdfDescriptor = ParcelFileDescriptor.open(cacheFile, ParcelFileDescriptor.MODE_READ_ONLY);
                    pdfRenderer = new PdfRenderer(pdfDescriptor);
                    
                    runOnUiThread(() -> {
                        if (viewPager.getAdapter() != null) viewPager.getAdapter().notifyDataSetChanged();
                        viewPager.setCurrentItem(0, true);
                        appendSystem("PDF loaded. Uploading to cloud parser...");
                    });

                    String parsedMarkdown = uploadPdfToCloud(cacheFile);
                    contextText = parsedMarkdown;
                    
                    runOnUiThread(() -> {
                        if (readerView != null) readerView.setText(contextText);
                        appendSystem("Cloud PDF parsing complete. Swipe right to view text.");
                    });
                } else {
                    StringBuilder sb = new StringBuilder();
                    try (FileInputStream fis = new FileInputStream(cacheFile)) {
                        byte[] buf = new byte[4096];
                        int len;
                        while ((len = fis.read(buf)) > 0) { sb.append(new String(buf, 0, len, StandardCharsets.UTF_8)); }
                    }
                    contextText = sb.toString();
                    
                    runOnUiThread(() -> {
                        if (readerView != null) readerView.setText(contextText);
                        viewPager.setCurrentItem(1, true);
                        appendSystem("Text loaded.");
                    });
                }
            } catch (Exception e) {
                runOnUiThread(() -> appendSystem("Failed to load: " + e.getMessage()));
            } finally {
                runOnUiThread(() -> setBusy(false));
            }
        }).start();
    }

    private String uploadPdfToCloud(File pdfFile) throws Exception {
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String parserEndpoint = prefs.getString(PREF_PARSER_ENDPOINT, DEFAULT_PARSER_ENDPOINT).trim();
        String parserApiKey = prefs.getString(PREF_PARSER_API_KEY, "").trim();

        // Strip trailing slash if any
        if (parserEndpoint.endsWith("/")) parserEndpoint = parserEndpoint.substring(0, parserEndpoint.length() - 1);
        // Fallback for generic upload if not DeconBear
        String uploadUrl = parserEndpoint.endsWith("/parse") ? parserEndpoint : parserEndpoint + "/parse";

        String boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW";
        HttpURLConnection conn = (HttpURLConnection) new URL(uploadUrl).openConnection();
        conn.setRequestMethod("POST");
        conn.setConnectTimeout(30000);
        conn.setReadTimeout(120000);
        conn.setDoOutput(true);
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        if (parserApiKey.length() > 0) {
            conn.setRequestProperty("Authorization", "Bearer " + parserApiKey);
            conn.setRequestProperty("X-API-Key", parserApiKey); // Used by DeconBear
        }

        try (OutputStream out = conn.getOutputStream()) {
            out.write(("--" + boundary + "\r\n").getBytes());
            out.write(("Content-Disposition: form-data; name=\"engine\"\r\n\r\nstruct\r\n").getBytes());
            out.write(("--" + boundary + "\r\n").getBytes());
            out.write(("Content-Disposition: form-data; name=\"file\"; filename=\"document.pdf\"\r\n").getBytes());
            out.write(("Content-Type: application/pdf\r\n\r\n").getBytes());
            
            try (FileInputStream fis = new FileInputStream(pdfFile)) {
                byte[] buf = new byte[8192];
                int len;
                while ((len = fis.read(buf)) > 0) { out.write(buf, 0, len); }
            }
            out.write(("\r\n--" + boundary + "--\r\n").getBytes());
        }

        int status = conn.getResponseCode();
        InputStream input = status >= 200 && status < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readStream(input, 5 * 1024 * 1024);
        if (status < 200 || status >= 300) {
            throw new IllegalStateException("API HTTP " + status + ": " + response);
        }

        try {
            JSONObject json = new JSONObject(response);
            
            // Check if it's DeconBear's async response
            if (json.has("task_id")) {
                String taskId = json.getString("task_id");
                int polls = 0;
                String baseEndpoint = uploadUrl.replace("/parse", "");
                
                while (polls < 600) {
                    Thread.sleep(5000);
                    polls++;
                    int currentPoll = polls;
                    runOnUiThread(() -> appendSystem("Cloud parsing in progress... (Poll " + currentPoll + ")"));
                    
                    HttpURLConnection sConn = (HttpURLConnection) new URL(baseEndpoint + "/status/" + taskId).openConnection();
                    sConn.setRequestProperty("X-API-Key", parserApiKey);
                    sConn.setConnectTimeout(10000);
                    sConn.setReadTimeout(10000);
                    
                    int sStatus = sConn.getResponseCode();
                    InputStream sInput = sStatus >= 200 && sStatus < 300 ? sConn.getInputStream() : sConn.getErrorStream();
                    JSONObject sJson = new JSONObject(readStream(sInput, 1024 * 1024));
                    String state = sJson.optString("status", "");
                    
                    if ("success".equals(state)) {
                        HttpURLConnection rConn = (HttpURLConnection) new URL(baseEndpoint + "/result/" + taskId).openConnection();
                        rConn.setRequestProperty("X-API-Key", parserApiKey);
                        rConn.setConnectTimeout(10000);
                        rConn.setReadTimeout(120000);
                        
                        InputStream rInput = rConn.getResponseCode() < 300 ? rConn.getInputStream() : rConn.getErrorStream();
                        JSONObject rJson = new JSONObject(readStream(rInput, 10 * 1024 * 1024));
                        return rJson.optString("markdown", "Empty markdown received");
                    } else if ("failure".equals(state)) {
                        throw new Exception("DocParser failed: " + sJson.optString("error", "Unknown error"));
                    }
                }
                throw new Exception("DocParser polling timed out after 50 minutes.");
            }
            
            // Synchronous fallback
            return json.optString("markdown", response);
        } catch (Exception e) {
            throw new Exception("Parse response error: " + e.getMessage());
        }
    }

    private void sendPrompt() {
        String prompt = promptInput.getText().toString().trim();
        if (prompt.length() == 0) return;

        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        String apiKey = prefs.getString(PREF_API_KEY, "").trim();
        String endpoint = prefs.getString(PREF_ENDPOINT, DEFAULT_ENDPOINT).trim();
        String model = prefs.getString(PREF_MODEL, DEFAULT_MODEL).trim();

        if (apiKey.length() == 0) {
            Toast.makeText(this, "Please configure Chat API key in Settings", Toast.LENGTH_SHORT).show();
            return;
        }

        promptInput.setText("");
        appendMessage("You", prompt);
        setBusy(true);

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

    private String callChatCompletions(String endpoint, String apiKey, String model, String prompt, String context) throws Exception {
        JSONObject payload = new JSONObject();
        payload.put("model", model);
        payload.put("temperature", 0.2);

        JSONArray messages = new JSONArray();
        messages.put(new JSONObject().put("role", "system").put("content", "You are KBase Mobile."));
        if (context != null && context.length() > 0) {
            messages.put(new JSONObject().put("role", "user").put("content", "Use this local context:\n\n" + context));
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
        try (OutputStream out = conn.getOutputStream()) { out.write(body); }

        int status = conn.getResponseCode();
        InputStream input = status >= 200 && status < 300 ? conn.getInputStream() : conn.getErrorStream();
        String response = readStream(input, 512 * 1024);
        if (status < 200 || status >= 300) { throw new IllegalStateException("HTTP " + status + ": " + response); }

        return new JSONObject(response).getJSONArray("choices").getJSONObject(0).getJSONObject("message").getString("content");
    }

    private String readStream(InputStream input, int maxBytes) throws Exception {
        if (input == null) return "";
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        byte[] chunk = new byte[4096];
        int total = 0;
        int read;
        while ((read = input.read(chunk)) != -1) {
            int allowed = Math.min(read, maxBytes - total);
            if (allowed > 0) { buffer.write(chunk, 0, allowed); total += allowed; }
            if (total >= maxBytes) break;
        }
        return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
    }

    private void setBusy(boolean busy) {
        progressBar.setVisibility(busy ? View.VISIBLE : View.GONE);
        sendButton.setEnabled(!busy);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_CONTEXT_FILE || resultCode != RESULT_OK || data == null || data.getData() == null) return;
        loadUri(data.getData());
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }
}
