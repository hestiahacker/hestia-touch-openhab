# Restore (openHAB 5)

1. Install the OS packages listed in `ADDONS_AND_SOFTWARE.md`.
2. Stop openHAB: `sudo systemctl stop openhab`
3. Restore the settings: `sudo openhab-cli restore hestia-openhab-v5.zip`
4. Extract the helper files: `sudo unzip -oq hestia-openhab-v5.zip -d /tmp/hestia 'home/*' 'setup/*'`
5. Install the helper files: `sudo /tmp/hestia/setup/install.sh`
6. Start openHAB: `sudo systemctl start openhab`
