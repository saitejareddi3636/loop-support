#!/bin/sh
# Run once on the existing Loopp Oracle host as ubuntu (Cloud Shell / console).
# Adds only the GitHub Actions deploy public key. Does not change firewall/NSG rules.
set -eu
KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAINOORs1hfwRaZSXX2jMkvdqcg+L9anuOk00xIDSVKwvt loopp-support-github-actions'
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"
if grep -qxF "$KEY" "$HOME/.ssh/authorized_keys"; then
  echo "Deploy key already present."
else
  echo "$KEY" >> "$HOME/.ssh/authorized_keys"
  echo "Deploy key appended."
fi
