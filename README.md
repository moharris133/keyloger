# keyloger
it is a key logging tool for windows and linux just for educational purpose and safely experimentation
# TOOL OVERVIEW
Command	Purpose
keystroke run	Start the keylogger
keystroke replay	Play back a captured log
keystroke sessions	List previous sessions
keystroke status	Check if a daemon is running
keystroke encrypt/decrypt	Encrypt/decrypt log files



## 🔧 Platform-Specific Setup
   Linux: sudo apt install python3-xlib python3-tk (for pynput)
   macOS: Grant Accessibility permissions in System Settings → Privacy & Security → Accessibility
   Windows: Runs out of the box (administrator rights recommended for cross-session capture)
## 🛡️ Security Features
AES-256-CBC on-disk encryption with PBKDF2 key derivation
RC4 fallback for lightweight encryption
PID-based daemon tracking so you don't lose the process
No network exfiltration included — that's for your C2 layer
Log rotation via sess# ⚠️ Disclaimer

**Keystroke** is a legitimate security testing tool designed for:

- Authorized penetration tests
- Red team engagements
- Security research & education
- Incident response forensics
- Personal device auditing (your own hardware)

## Legal Notice

This tool intercepts keystrokes and user input. **Using it without explicit
consent is a crime.** You must have written authorization from the device owner
before deploying this software on any system you do not personally own.

### Illegal uses include (but are not limited to):

- Installing on a spouse's/partner's device without their knowledge
- Deploying on employee workstations without a signed testing agreement
- Using on school or university lab machines without prior authorization
- Monitoring any person without their explicit, informed consent
- Capturing credentials or personal data for unauthorized purposes

### Penalties for unauthorized use:

| Jurisdiction | Maximum Penalty |
|---|---|
| United States | $250,000 fine + 10 years imprisonment (CFAA/ECPA) |
| United Kingdom | Unlimited fine + 10 years imprisonment (CMA 1990) |
| EU Member States | €20M or 4% of global turnover (GDPR) |
| Australia | $444,000 fine + 10 years imprisonment (Criminal Code Act) |

## Liability Waiver

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

**You are responsible for everything this tool does under your control.**

## Ethical Use Checklist

- [ ] I have a signed authorization letter / testing agreement
- [ ] The scope of testing is clearly defined in writing
- [ ] I have informed the appropriate point of contact
- [ ] I am testing within the agreed time window
- [ ] I am only targeting systems explicitly listed in the scope
- [ ] I will securely destroy all captured data after the engagement
- [ ] I will report findings responsibly to the system owner

> If you cannot check every box, **do not use this tool.**ion timestamps





pip install pyperclip       # clipboard monitoring (optional)
pip install pyyaml          # YAML output format (optional)
