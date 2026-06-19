#!/bin/bash
# Reintenta crear una VM Ampere A1 (Always Free) hasta que Oracle libere cupo.
# Llenar los 5 valores antes de correr.

COMPARTMENT_ID="<TU_COMPARTMENT_OCID>"
# Lista de availability domains a probar en orden (la cuenta free suele tener
# capacidad disponible en unas ADs si y en otras no, asi que se reintenta
# rotando entre todas las que tengas en tu region).
AVAILABILITY_DOMAINS=(
  "<TU_AD_1, ej: xxxx:US-ASHBURN-AD-1>"
  "<TU_AD_2, ej: xxxx:US-ASHBURN-AD-2>"
  "<TU_AD_3, ej: xxxx:US-ASHBURN-AD-3>"
)
IMAGE_ID="<ID_DE_LA_IMAGEN_UBUNTU_ARM>"
SUBNET_ID="<ID_DE_TU_SUBRED>"
SSH_PUBLIC_KEY_FILE="$HOME/.ssh/id_rsa.pub"   # ajusta si tu llave tiene otro nombre

while true; do
  for AD in "${AVAILABILITY_DOMAINS[@]}"; do
    echo "$(date) - intentando crear instancia en $AD..."
    oci compute instance launch \
      --compartment-id "$COMPARTMENT_ID" \
      --availability-domain "$AD" \
      --shape "VM.Standard.A1.Flex" \
      --shape-config '{"ocpus":4,"memoryInGBs":24}' \
      --display-name "betbot-vps" \
      --image-id "$IMAGE_ID" \
      --subnet-id "$SUBNET_ID" \
      --assign-public-ip true \
      --ssh-authorized-keys-file "$SSH_PUBLIC_KEY_FILE" \
      && { echo "EXITO: instancia creada en $AD."; exit 0; }

    echo "Sin cupo en $AD, probando siguiente AD..."
  done
  echo "Sin cupo en ninguna AD, reintentando todas en 60s..."
  sleep 60
done
