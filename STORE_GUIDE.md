* [ ] 

# Microsoft Store Submission Guide — Read Selected

**Developer: Giorgi Chikovani**

This guide walks through everything needed to get the reader app published in
the Microsoft Store, split into three phases: account setup, build, and submission.

---

## Phase 1 — One-time account setup

### 1.1 Create a Microsoft Partner Center account

1. Go to [https://partner.microsoft.com/dashboard](https://partner.microsoft.com/dashboard)
2. Sign in with (or create) a Microsoft account.
3. Choose **Individual** developer (not company) unless you have a registered company.
4. Pay the one-time registration fee (~$19 USD for individuals).
   - Only needed once; covers all future app submissions.
5. Fill in your name: **Giorgi Chikovani** and complete identity verification.

### 1.2 Reserve the app name

1. In Partner Center go to **Apps and games → New product → MSIX or PWA app**.
2. Enter the app name: **Read Selected**
3. Click **Reserve product name** — this secures the name before you submit.

### 1.3 Copy the package identity into the manifest

1. In the reserved app go to **Product management → Product identity**.
2. Copy these values into `store\AppxManifest.xml` exactly:| Product identity field                  | Manifest attribute                |
   | --------------------------------------- | --------------------------------- |
   | Package/Identity/Name                   | `<Identity Name="...">`         |
   |                                         |                                   |
   | Package/Identity/Publisher              | `<Identity Publisher="CN=...">` |
   | Package/Properties/PublisherDisplayName | `<PublisherDisplayName>`        |
3. If these don't match, the upload is rejected.

---

## Phase 2 — Install prerequisites (once per machine)

### 2.1 Windows SDK (for makeappx + signtool)

```powershell
winget install --id Microsoft.WindowsSDK.10.0.22621
```

Or download from [https://developer.microsoft.com/windows/downloads/windows-sdk/](https://developer.microsoft.com/windows/downloads/windows-sdk/)

### 2.2 PyInstaller



```PowerShell
uv pip install pyinstaller
```



### 2.3 Voice files (required at build time)

The .msix must ship at least one Piper voice or the app shows "no voices found" on
first launch. Download one or more voices **before** building:

**English (recommended default):**

```
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high/en_US-ryan-high.onnx.json
```

Place both files in `reader\voices\`.

**Georgian (optional):**

```
https://huggingface.co/rhasspy/piper-voices/resolve/main/ka/ka_GE/natia/medium/ka_GE-natia-medium.onnx
https://huggingface.co/rhasspy/piper-voices/resolve/main/ka/ka_GE/natia/medium/ka_GE-natia-medium.onnx.json
```

Place both files in `reader\voices\`.

> **Note on size**: Each voice is 60–150 MB. The Store has a 25 GB package size
> limit, so bundling several voices is fine. You can also let users download
> voices themselves — in that case ship the app with just one default voice.

---

## Phase 3 — Build the package

From the repo root in a PowerShell terminal:

```powershell
# Standard build (for Store upload — NOT signed):
.\store\build_msix.ps1

# Build + sign locally (for testing the install on your own machine first):
.\store\build_msix.ps1 -SignForTesting
```

The script:

1. Runs PyInstaller → `dist\reader\`
2. Generates all required Store icons → `store\Assets\`
3. Assembles → `msix_staging\`
4. Packages → `dist\ReadSelected_1.0.0.0.msix`

**To test locally before uploading:**

```powershell
# First run (installs test cert as Trusted Root CA — admin required once):
Import-PfxCertificate -FilePath 'store\TestCert.pfx' `
  -CertStoreLocation Cert:\LocalMachine\TrustedPeople `
  -Password (ConvertTo-SecureString 'test' -Force -AsPlainText)

# Then install the .msix:
Add-AppxPackage dist\ReadSelected_1.0.0.0.msix
```

Or just double-click `dist\ReadSelected_1.0.0.0.msix`.

**To uninstall the test build:**

```powershell
Get-AppxPackage *ReadSelected* | Remove-AppxPackage
```

---

## Phase 4 — Store submission

### 4.1 Create a new submission

1. In Partner Center, open the reserved **Read Selected** app.
2. Click **Start submission**.

### 4.2 Pricing and availability

- **Markets**: all markets, or select specific ones.
- **Price**: Free (recommended to start).
- **Visibility**: Public.

### 4.3 Properties

- **Category**: Utilities & Tools → Productivity
- **Sub-category**: Tools
- **Privacy policy URL**: required. If you don't have a website yet, create a simple
  GitHub Gist or GitHub Pages page that says the app does not collect or transmit
  user data. The app stores only its own config locally in `%APPDATA%\WinThings\reader\`.
- **Support contact**: your email or GitHub issues URL.

### 4.4 Store listing (en-us)

| Field                               | Text                                                                                                                                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Title                               | Read Selected                                                                                                                                                                                           |
| Short description (up to 270 chars) | Select any text anywhere on your PC and press Ctrl+Shift+A to hear it read aloud in a natural neural voice. Fully offline, no telemetry. English and Georgian voices included, more available for free. |
| Description                         | See `store\description.txt` for the full long description.                                                                                                                                            |
| Keywords                            | text to speech, TTS, read selected text, accessibility, Piper, offline voice, Georgian                                                                                                                  |

**Screenshots** (required — at least 1, up to 10 per device family):

- Take screenshots with the settings window open, the reading-mode overlay active,
  and the tray icon menu showing. Minimum size: 1366 × 768 px.

### 4.5 Packages

Upload the **unsigned** `dist\ReadSelected_1.0.0.0.msix`.
Partner Center re-signs the package automatically during certification.

### 4.6 Submit for certification

Click **Submit to the Store**. Certification takes 1–3 business days for new apps.

---

## What Microsoft certifies

The app will be tested against these policies (all of which it should pass):

| Check                                 | Status                                               |
| ------------------------------------- | ---------------------------------------------------- |
| Runs at full trust (`runFullTrust`) | ✅ declared in manifest                              |
| No undefined capabilities             | ✅                                                   |
| Global hotkey via `RegisterHotKey`  | ✅ (no low-level hook — clean)                      |
| Clipboard access                      | ✅ (full-trust apps may use the clipboard)           |
| UI Automation                         | ✅ (full-trust apps may call UIA)                    |
| Subprocess (`piper.exe`)            | ✅ bundled in the package                            |
| `%APPDATA%` writes                  | ✅ MSIX redirects them transparently                 |
| No crash on launch                    | test with `.\store\build_msix.ps1 -SignForTesting` |

---

## Versioning for updates

Bump `Version` in `store\AppxManifest.xml` before each submission:

```
1.0.0.0 → 1.1.0.0  (minor feature update)
1.0.0.0 → 2.0.0.0  (major change)
```

The last two digits (`.0.0`) are reserved; set them to 0.

Then rebuild:

```powershell
.\store\build_msix.ps1 -Version "1.1.0.0"
```

---

## Troubleshooting

| Problem                            | Fix                                                                                 |
| ---------------------------------- | ----------------------------------------------------------------------------------- |
| `makeappx` not found             | Install Windows SDK (§2.1)                                                         |
| Upload rejected: identity mismatch | Copy Name/Publisher from Product identity into `store\AppxManifest.xml` (§1.3)   |
| App installs but doesn't start     | Check `%APPDATA%\WinThings\reader\reader.log`                                     |
| Hotkey not working after install   | Try a different hotkey in Settings; another app may own Ctrl+Shift+A                |
| "no voices found" on first launch  | Ensure `.onnx` and `.onnx.json` files are in `reader\voices\` before building |
| Certification fails: crash         | Run the signed .msix on a clean Windows VM before submitting                        |
