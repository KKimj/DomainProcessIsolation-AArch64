#!/bin/bash

export WORKSPACE=./fvp
export IMAGE=$WORKSPACE/build-poky/tmp-poky/deploy/images/fvp-base/Image
export BL1=$WORKSPACE/build-poky/tmp-poky/deploy/images/fvp-base/bl1-fvp.bin
export FIP=$WORKSPACE/build-poky/tmp-poky/deploy/images/fvp-base/fip-fvp.bin
export DISK=$WORKSPACE/build-poky/tmp-poky/deploy/images/fvp-base/core-image-minimal-fvp-base.disk.img
export DTB=$WORKSPACE/build-poky/tmp-poky/deploy/images/fvp-base/fvp-base-gicv3-psci-custom.dtb
export NET=1

f_error () { echo "*ERROR* $1"; exit 1; }
[[ ! -x $MODEL ]] && f_error "Cannot run $MODEL"
[[ ! -f $DISK ]] && f_error "Not found $DISK"

./$WORKSPACE/run-scripts/fvp/run_model.sh