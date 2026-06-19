#!/bin/bash
# Reintenta crear una VM Ampere A1 (Always Free) hasta que Oracle libere cupo.
# Llenar los 5 valores antes de correr.

COMPARTMENT_ID="<TU_COMPARTMENT_OCID>"
AVAILABILITY_DOMAIN="<TU_AD, ej: xxxx:US-ASHBURN-AD-1>"
IMAGE_ID="<ID_DE_LA_IMAGEN_UBUNTU_ARM>"
SUBNET_ID="<ID_DE_TU_SUBRED>"
SSH_PUBLIC_KEY_FILE="$HOME/.ssh/id_ed25519.pub"   # ajusta si tu llave tiene otro nombre

while true; do
  echo "$(date) - intentando crear instancia..."
  oci compute instance launch \
    --compartment-id "$COMPARTMENT_ID" \
    --availability-domain "$AVAILABILITY_DOMAIN" \
    --shape "VM.Standard.A1.Flex" \
    --shape-config '{"ocpus":4,"memoryInGBs":24}' \
    --display-name "betbot-vps" \
    --image-id "$IMAGE_ID" \
    --subnet-id "$SUBNET_ID" \
    --assign-public-ip true \
    --ssh-authorized-keys-file "$SSH_PUBLIC_KEY_FILE" \
    && { echo "EXITO: instancia creada."; break; }

  echo "Sin cupo disponible, reintentando en 60s..."
  sleep 60
done
