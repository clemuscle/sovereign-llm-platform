#!/usr/bin/env bash
# Couche 2 (docs/gpu-on-k3s.md) sur le poste local : NVIDIA Container Toolkit dans WSL2.
# Le driver est celui de Windows : WSL2 expose le GPU par /dev/dxg et /usr/lib/wsl/lib,
# sans /dev/nvidia* ni module noyau. Ne rien installer de NVIDIA côté driver dans WSL.
#   sudo infra/local/gpu-host.sh
# Rejouable : ne réinstalle pas un toolkit déjà à la bonne version, ne redémarre k3s que si
# son containerd n'a pas encore le runtime nvidia.
set -euo pipefail

TOOLKIT_VERSION=1.20.1-1
KEYRING=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
LIST=/etc/apt/sources.list.d/nvidia-container-toolkit.list
CONTAINERD_CONFIG=/var/lib/rancher/k3s/agent/etc/containerd/config.toml

[[ $EUID -eq 0 ]] || { echo "À lancer avec sudo." >&2; exit 1; }
[[ -e /dev/dxg ]] || { echo "/dev/dxg absent : pas de GPU exposé à WSL2 (driver Windows ?)." >&2; exit 1; }

if [[ ! -f $LIST ]]; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o "$KEYRING"
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed "s#deb https://#deb [signed-by=$KEYRING] https://#" > "$LIST"
fi

if [[ $(dpkg-query -W -f '${Version}' nvidia-container-toolkit 2>/dev/null) != "$TOOLKIT_VERSION" ]]; then
  apt-get update
  apt-get install -y \
    nvidia-container-toolkit="$TOOLKIT_VERSION" nvidia-container-toolkit-base="$TOOLKIT_VERSION" \
    libnvidia-container-tools="$TOOLKIT_VERSION" libnvidia-container1="$TOOLKIT_VERSION"
fi

# k3s cherche nvidia-container-runtime au démarrage et l'ajoute à sa config containerd générée.
if ! grep -q nvidia-container-runtime "$CONTAINERD_CONFIG"; then
  systemctl restart k3s
  for _ in $(seq 30); do grep -q nvidia-container-runtime "$CONTAINERD_CONFIG" 2>/dev/null && break; sleep 2; done
fi
grep -q nvidia-container-runtime "$CONTAINERD_CONFIG" \
  || { echo "k3s n'a pas détecté nvidia-container-runtime." >&2; exit 1; }

echo "Toolkit $TOOLKIT_VERSION en place, runtime nvidia dans containerd. Suite : infra/local/gpu.sh"
