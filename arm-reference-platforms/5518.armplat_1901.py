#!/usr/bin/env python3

"""
 " Copyright (c) 2019, Arm Limited. All rights reserved.
 " Author: Ash Wilding <ash.wilding@arm.com>
 "
 " SPDX-License-Identifier: BSD-3-Clause
"""

fstr = f"If you get a SyntaxError here, you need to upgrade to Python 3.6+"

from sys import version_info as PYVER
assert PYVER >= (3,6), "script requires Python 3.6+"

import argparse
import bz2
import collections, ctypes
import gzip
import hashlib
import logging
import math
import os
import platform
import re
import shutil, subprocess, sys
import tarfile, threading, time
import urllib.error, urllib.request
import zipfile

HOST = platform.system()
assert HOST in ["Windows", "Linux"], "script requires Windows or Linux"
if HOST=="Windows":
    import ctypes


###
 # Database structure describing each available software stack configuration.
 #
 # Keys defined by a node are inherited by that node's children, unless the
 # child overrides it by defining the same key. Keys inherited by a node are
 # also inherited by that node's children using the same mechanism.
 #
 # Example:
 #
 #      "people": {
 #          "surname": "Smith",
 #          "john": {
 #              "name": "John",
 #          },
 #          "jane": {
 #              "name": "Jane",
 #          },
 #          "alexa": {
 #              "name": "Alexa",
 #              "surname": "Smith-Doe",
 #          },
 #      },
 #
 #      Lookup "people.john.surname"  --> "Smith"
 #      Lookup "people.jane.surname"  --> "Smith"
 #      Lookup "people.alexa.surname" --> "Smith-Doe"
 #
 # Keys can cross-reference other keys in the database by "{wrapping}" in curly
 # braces. A single key may comprise multiple cross-references.
 #
 # Example:
 #
 #      "arm": {
 #          "release": "18.10",
 #          "url": "https://example.com/",
 #          "title": "Arm Platforms Software {release}",
 #      }
 #      "something": {
 #          "url": "{arm.url}/path/to/{arm.release}/123.zip",
 #      }
 #
 #      Lookup "arm.title" --> "Arm Platforms Software 18.10"
 #      Lookup "something.url" --> "https://example.com/path/to/18.10/123.zip"
 #
 # Keys can comprise both forward-references and backward-references.
 #
 # Example:
 #
 #      "thing": {
 #          "url": "https://example.com",
 #          "name0": "AAA",
 #          "name: "{name0}-{name1}.zip",
 #
 #          "variant1": {
 #              "name1": "111",
 #          }
 #
 #          "variant2": {
 #              "name1": "222",
 #          }
 #
 #          "variant3": {
 #              "name0": "BBB",
 #              "name1": "333",
 #          }
 #      }
 #
 # Here each variant backward-references "url" --> "https://example.com"
 #
 # However, they each inherit key "name" which is dynamically resolved using
 # the "name0" and "name1" defined by the particular child being referenced:
 #
 #      Lookup "thing.variant1.name" --> "AAA-111.zip"
 #      Lookup "thing.variant2.name" --> "AAA-222.zip"
 #      Lookup "thing.variant3.name" --> "BBB-333.zip"
 #
 # Cross-references can use the special "@" character to refer to the platform
 # key passed to the lookup function.
 #
 # Example:
 #
 #      "fedora": {
 #          "name": "Fedora Server",
 #          "url": "https://path.to.fedora/server/images/{@.fedora.vsn}",
 #      }
 #      "p": {
 #          "board": {
 #              "name": "Example Board",
 #              "fedora": {
 #                  "vsn": "27",
 #              },
 #          },
 #          "model": {
 #              "name": "Example Model",
 #              "fedora": {
 #                  "vsn": "28",
 #              },
 #          },
 #      }
 #
 #      Lookup "fedora.url" with plat="p.board" -->
 #          "https://path.to.fedora/server/images/27"
 #      Lookup "fedora.url" with plat="p.model" -->
 #          "https://path.to.fedora/server/images/28"
 #
 # So far all examples have been string substitutions, however cross-references
 # can also point at other dicts and lists by prefixing the {curly} braces with
 # a hash/pound sign #:
 #
 #      "p.board": {
 #          "name": "Example board platform",
 #          "k": [
 #              "k.kernel1", "k.kernel2",
 #          ],
 #      },
 #      "p.model": {
 #          "name": "Example model platform",
 #          "k": "#{p.board.k}",
 #      }
 #
 #      Lookup "p.model.k" --> ["k.kernel1", "k.kernel2"]
 #
 # Finally, keys exactly matching string "null" will return Python's None.
###
ARMPLATDB = {
  "arm": {
    "rel": "19.01",
    "vsn": "19.01c",
    "cms": "cms.developer.arm.com",
  },

  "linaro": {
    "lt": "Linaro/ArmLT",
    "url": "releases.linaro.org",
  },

  "host": {
    "linux": {
      "pkgs": {
        "apt": {
          "bison", "dh-autoreconf", "device-tree-compiler", "expect", "flex",
          "fuseext2", "g++", "gcc", "git", "libssl-dev", "openjdk-8-jdk",
          "python-crypto", "python-wand", "uuid-dev", "xterm",
        },
      },
    },
  },

  ###
   # Platforms that can be chosen by the user.
   #
   # Each platform must define:
   #
   #    name: display name printed in menus
   #    manifests: URL of git repository containing platform's manifests
   #    pdir: build script directory
   #    k: list of supported kernels (or "null")
   #    fs: list of supported userspace filesystems (or "null")
   #    fw: list of supported firmware & test suites (or "null")
   #    pb: list of supported prebuilt configurations (or "null")
   #
   # Platforms may optionally define:
   #
   #    deps: list of required downloads (such as compilers)
   #    pihooks: list of post-init hook functions to run (see class pihooks)
   #
   # Further, platforms advertising support for certain kernels and userspace
   # filesystems must define additional keys.
   #
   # If advertising support for k.mainline, the platform must define:
   #
   #    linux.vsn: which release of mainline Linux kernel
   #
   # If advertising support for k.ack, the platform must define:
   #
   #    ack.code: manifest code i.e. pinned-{code}.xml
   #    ack.vsn: which release of Android Common Kernel
   #    ack.commit: which specific commit of Android Common Kernel {vsn}
   #
   # If advertising support for any variant of fs.oe, the platform must define:
   #
   #    oe.rel: OpenEmbedded release
   #    oe.vsn: OpenEmbedded version
   #    oe.url: URL of OpenEmbedded downloads
   #    oe.md5name: MD5 checksum file name
   #
   # If advertising support for fs.android, the platform must define:
   #
   #    android.rel: Android release
   #    android.codename: Android code name e.g. Marshmallow, Oreo, Pie
   #    android.url: URL of Android downloads
   #    android.rootfs: root filesystem image name
   #    android.ramdisk.dir: ramdisk destination directory
  ###
  "p": {
    "name": "Platforms",

    ### Default keys inherited by all platforms and optionally overridden
    "mrel": "{arm.rel}",
    "manifests": "https://git.linaro.org/landing-teams/working/arm/manifest",
    "pburl": "{linaro.url}/members/arm/platforms/{arm.rel}",
    "ack": {
      "code": "ack",
      "vsn": "ack-4.9-armlt",
      "commit": "d4c7d1c81b",
    },
    "pihooks": "null",

    ### Boards
    "board": {
      "name": "Development boards",

      ### Juno
      "juno": {
        "name": "Juno",
        "includes": [
          "oc.mb",
          "oc.scp.juno",
          "oc.tfa",
          "oc.optee.os",
          "oc.optee.client",
        ],
        "mb": {
          "commit": "ca486a5a90",
        },

        ### Juno with 64-bit SW
        "64b": {
          "name": "Juno with 64-bit software stack",
          "pdir": "juno",
          "deps": [
            "dl.tool.gcc.a64", "dl.tool.gcc.a32",
          ],
          "ack": {
            "code": "juno",
            "vsn": "ack-4.14-armlt",
            "commit": "cb9214c4c3",
          },
          "android": {
            "rel": "19.01",
            "codename": "Pie",
            "rootfs": "juno.img.bz2",
            "url": "{linaro.url}/members/arm/android/juno/{rel}",
            "ramdisk": {
              "dir": "prebuilts/android/juno",
            },
          },
          "oe": {
            "rel": "17.01",
            "vsn": "5.2_20170127-761",
            "url": "{linaro.url}/openembedded/juno-lsk/{rel}",
            "md5name": "{dl.img.oe.name}.md5",
          },
          "k": [
            "k.ack", "k.latest"
          ],
          "fs": [
            "fs.busybox", "fs.oe.mini", "fs.oe.lamp", "fs.android",
          ],
          "fw": [
            "fw.edkii",
          ],
          "pb": [
            "pb.ack.android",
            "pb.latest.busybox",
            "pb.latest.oe.mini",
            "pb.latest.oe.lamp",
            "pb.edkii",
          ],
        },

        ### Juno with 32-bit SW
        "legacy": {
          "name": "Juno with legacy 32-bit software stack",
          "pdir": "juno32",
          "deps": [
            "dl.tool.gcc.a64", "dl.tool.gcc.a32",
          ],
          "oe": {
            "rel": "15.07",
            "vsn": "4.9_20150725-725",
            "url": "{linaro.url}/openembedded/vexpress-lsk/{rel}",
          },
          "k": [
            "k.ack", "k.latest",
          ],
          "fs": [
            "fs.busybox", "fs.oe.alip",
          ],
          "fw": "null",
          "pb": [
            "pb.ack.busybox",
            "pb.latest.busybox",
            "pb.latest.oe.alip",
          ],
        },
      },

      ### Unsupported boards
      "unsup": {
        "name": "Unsupported boards",

        ### TC2
        "tc2": {
          "name": "TC2 with legacy 32-bit software stack",
          "pdir": "tc2",
          "deps": [
            "dl.tool.gcc.a32",
          ],
          "android": {
            "rel": "6.0-15.11",
            "codename": "Marshmallow",
            "rootfs": "vexpress.img.bz2",
            "url": "{linaro.url}/android/reference-lcr/vexpress/{rel}",
            "ramdisk": {
              "dir": "prebuilts/android/tc2",
            },
          },
          "oe": "#{p.board.juno.legacy.oe}",
          "k": "#{p.board.juno.legacy.k}",
          "fs": [
            "fs.busybox", "fs.oe.alip", "fs.android",
          ],
          "fw": [
            "fw.edkii",
          ],
          "pb": "null",
        },
      },
    },

    ### Fixed Virtual Platforms (FVPs)
    "fvp": {
      "name": "Fixed Virtual Platforms (FVPs)",

      ### Armv8 architecture FVPs
      "v8a": {
        "name": "Armv8 architecture",
        "pdir": "fvp",
        "includes": [
          "oc.tfa", "oc.optee.os", "oc.optee.client",
        ],

        ### Armv8-A Base Platform
        "base": {
          "name": "Armv8-A Base Platform",
          "descr": "11.3.30+ (Rev C)",

          ### Armv8-A Base Platform with 64-bit SW
          "64b": {
            "name": "Armv8-A Base Platform with 64-bit software stack",
            "deps": [
              "dl.tool.gcc.a64", "dl.tool.gcc.a32",
            ],
            "descr": "null",
            "android": {
              "rel": "7.0-16.10",
              "codename": "Nougat",
              "rootfs": "fvp.img.bz2",
              "url": "{linaro.url}/android/reference-lcr/fvp/{rel}",
              "ramdisk": {
                "dir": "prebuilts/android/fvp",
              },
            },
            "oe": {
              "rel": "15.09",
              "vsn": "4.9_20150912-729",
              "url": "{linaro.url}/openembedded/juno-lsk/{rel}",
              "md5name": "MD5SUMS.txt",
            },
            "k": [
              "k.ack", "k.latest",
            ],
            "fs": [
              "fs.busybox", "fs.oe.mini", "fs.oe.lamp", "fs.android",
            ],
            "fw": [
              "fw.edkii",
            ],
            "pb": [
              #"pb.ack.android.debug",      # Temporarily unavailable
              "pb.ack.busybox",
              "pb.latest.busybox",
              #"pb.latest.oe.mini.debug",   # Temporarily unavailable
              #"pb.latest.oe.lamp.debug",   # Temporarily unavailable
              "pb.edkii",
            ],
          },

          ### Armv8-A Base Platform with 32-bit SW
          "legacy": {
            "name": "Armv8-A Base Platform with legacy 32-bit software stack",
            "pdir": "fvp32",
            "deps": [
              "dl.tool.gcc.a32",
            ],
            "descr": "null",
            "oe": "#{p.board.juno.legacy.oe}",
            "k": "#{p.board.juno.legacy.k}",
            "fs": "#{p.board.juno.legacy.fs}",
            "fw": "null",
            "pb": "#{p.board.juno.legacy.pb}",
          },
        },

        ### Foundation Model
        "fndn": {
          "name": "Armv8-A Foundation Model",
          "descr": "11.3.30+",

          ### Foundation Model with 64-bit SW
          "64b": {
            "name": "Armv8-A Foundation Model with 64-bit software stack",
            "deps": [
              "dl.tool.gcc.a64", "dl.tool.gcc.a32",
            ],
            "descr": "null",
            "android": "#{p.fvp.v8a.base.64b.android}",
            "oe": "#{p.fvp.v8a.base.64b.oe}",
            "k": "#{p.fvp.v8a.base.64b.k}",
            "fs": "#{p.fvp.v8a.base.64b.fs}",
            "fw": "#{p.fvp.v8a.base.64b.fw}",
            "pb": "#{p.fvp.v8a.base.64b.pb}",
          },
        },
      },

      ### System Guidance
      "sg": {
        "name": "System Guidance",
        "deps": [
          "dl.tool.gcc.a64", "dl.tool.gcc.a32", "dl.tool.gcc.scp.5",
        ],

        ### System Guidance for Infrastructure (SGI)
        "i": {
          "name": "System Guidance for Infrastructure (SGI)",
          "includes": [
            "oc.tfa", "oc.scp",
          ],

          ### SGI-575
          "575": {
            "name": "SGI-575",
            "pdir": "sgi575",
            "linux": {
              "vsn": "4.20",
            },
            "fedora": {
              "rel": "27",
              "vsn": "27-1.6",
            },
            "k": [
              "k.mainline",
            ],
            "fs": [
              "fs.busybox", "fs.fedora",
            ],
            "pb": [
              "pb.latest.busybox.edkii",
            ],
            "fw": "null",
          },
        },

        ### System Guidance for Mobile (SGM)
        "m": {
          "name": "System Guidance for Mobile (SGM)",
          "includes": [
            "oc.tfa", "oc.optee.os", "oc.optee.client", "oc.scp",
          ],

          ### SGM-775
          "775": {
            "name": "SGM-775",
            "pdir": "sgm775",
            "ack": {
              "vsn": "ack-4.9-armlt-18.10",
              "commit": "null",
            },
            "android": {
              "rel": "(built from source)",
              "codename": "Oreo",
            },
            "k": [
              "k.ack.sgm775.busybox", "k.ack.sgm775.android",
            ],
            "fs": [
              "fs.android.bfs", "fs.busybox",
            ],
            "pb": [
              "pb.ack.busybox", "pb.ack.android.big",
            ],
            "fw": "null",
          },
        },
      },
    },

    ### All supported platforms
    ### Platforms need to be in this list to appear in the menus
    "all": [
      "p.board.juno.64b", "p.board.juno.legacy", "p.board.unsup.tc2",
      "p.fvp.v8a.base.64b", "p.fvp.v8a.base.legacy", "p.fvp.v8a.fndn.64b",
      "p.fvp.sg.i.575", "p.fvp.sg.m.775",
    ],
  },

  ###
   # Kernels that can be chosen by the user.
   #
   # Each kernel must define:
   #
   #    name: display name printed in menus
   #    manifest: repo manifest file name
   #    fs: list of supported userspace filesystems
   #
   # Kernels may optionally define:
   #
   #    descr: description printed in menus
   #
   # Note: the list of userspace filesystems actually presented to the user is
   # the intersection between what both the platform and kernel support.
  ###
  "k": {
    "name": "Linux kernel & userspace filesystem",
    "priority": 51,

    ### Android Common Kernel (ACK)
    "ack": {
      "name": "{linaro.lt} Android Common Kernel",
      "vsn": "{@.ack.vsn}",
      "commit": "{@.ack.commit}",
      "manifest": "pinned-{@.ack.code}.xml",
      "fs": [
        "fs.busybox",
        "fs.oe.alip",
        "fs.oe.mini",
        "fs.oe.lamp",
        "fs.android",
        "fs.android.bfs",
      ],

      ### SGM-775 variants for running BusyBox or Android respectively
      "sgm775": {
        "busybox": {
          "manifest": "sgm775.xml",
        },
        "android": {
          "manifest": "sgm775-android.xml",
        },
      },
    },

    ### Latest landing team kernel
    "latest": {
      "name": "{linaro.lt} Latest Stable Kernel",
      "vsn": "4.19",
      "commit": "e97e8d868a",
      "manifest": "pinned-latest.xml",
      "fs": [
        "fs.busybox", "fs.oe.alip", "fs.oe.mini", "fs.oe.lamp",
      ],
    },

    ### Mainline Linux
    "mainline": {
      "name": "Mainline Kernel",
      "vsn": "{@.linux.vsn} master",
      "commit": "null",
      "manifest": "pinned-{@.pdir}.xml",
      "fs": [
        "fs.busybox", "fs.fedora",
      ],

      ### Prebuilts containing Mainline were built on a particular date
      "pb": {
        "vsn": "{@.linux.vsn} snapshot of master built on 2019-02-19",
      }
    },
  },


  ###
   # Filesystems that can be chosen by the user.
   #
   # Each filesystem must define:
   #
   #    name: display name printed in menus
   #    script: build script file name
   #
   # Filesystems may optionally define:
   #
   #    descr: description printed in menus
   #    deps: list of required downloads
  ###
  "fs": {
    "priority": 61,

    ### Android
    "android": {
      "name": "Android",
      "vsn": "{@.android.codename} {@.android.rel}",
      "commit": "null",
      "script": "android",
      "deps": [
        "dl.img.android.rootfs", "dl.img.android.ramdisk"
      ],

      ### Build from source variant
      "bfs": {
        "deps": "null",
        "commit": "{@.android.commit}",
      },
    },

    ### BusyBox
    "busybox": {
      "name": "BusyBox",
      "vsn": "null",
      "commit": "111cdcf295",
      "script": "busybox",
    },

    ### OpenEmbedded
    "oe": {
      "name": "OpenEmbedded",
      "vsn": "{@.oe.rel} {@.oe.vsn}",
      "commit": "null",
      "script": "oe",

      ### ALIP variant
      "alip": {
        "name": "OpenEmbedded ALIP",
        "deps": [
          "dl.img.oe.alip",
        ],
      },

      ### Minimal variant
      "mini": {
        "name": "OpenEmbedded Minimal",
        "deps": [
          "dl.img.oe.mini"
        ],
      },

      ### LAMP variant
      "lamp": {
        "name": "OpenEmbedded LAMP",
        "deps": [
          "dl.img.oe.lamp"
        ],
      },
    },

    ### Fedora Server
    "fedora": {
      "name": "Fedora Server",
      "vsn": "{@.fedora.vsn}",
      "commit": "null",
       # Fedora doesn't have a build script; we just need to get UEFI installed
       # and then boot the iso. Unfortunately SGI platforms don't have a fw.edkii
       # config so we'll need to build a kernel and userspace filesystem even
       # though it won't be used. Use BusyBox as it has no deps and builds fast.
      "script": "busybox",
      "deps": [
        "dl.img.fedora",
      ],
    },
  },


  ###
   # Firmware and test suites that can be chosen by the user.
   #
   # Each firmware must define:
   #
   #    name: display name printed in menus
   #    stubfs: build script file name
   #    manifest: repo manifest file name
   #
   # Firmware may optionally define:
   #
   #    descr: description printed in menus
  ###
  "fw": {
    "name": "Firmware & test suites",

    ### EDK II UEFI
    "edkii" : {
      "name": "EDK II UEFI",
      "vsn": "edk2-stable201811",
      "commit": "005c855dc6",
      "priority": 31,
      "stubfs": "uefi",
      "manifest": "pinned-{stubfs}.xml",
      "includes": [
        "fw.edkii.platforms",
      ],

      ### Platform-specific code
      "platforms": {
        "name": "EDK II Platforms",
        "vsn": "null",
        "commit": "80f6be6eb1",
        "priority": 32,
        "includes": "null",
      },
    },
  },


  ###
   # Prebuilt configurations that can be chosen by the user.
   #
   # Each prebuilt configuration must define:
   #
   #    name: display name printed in menus
   #    deps: list of required downloads
  ###
  "pb": {

    ### ACK configs
    "ack": {
      "name0": "{k.ack.name}",

      ### U-Boot + ACK + Android
      "android": {
        "name": "{name0} + {fs.android.name}",
        "deps": [
          "dl.img.android.rootfs", "dl.archive.ack.android",
        ],
        "includes": [
          "oc.uboot", "k.ack", "fs.android",
        ],

        ### Debug variant
        "debug": {
          "deps": [
            "dl.img.android.rootfs",
            "dl.archive.ack.android",
            "dl.archive.ack.android.debug",
          ],
        },

        ### Fully-packaged variant (already contains rootfs image)
        "big": {
          "name": "{name0} + {fs.android.name}",
          "deps": [
            "dl.archive.ack.android",
          ],
        },
      },

      ### U-Boot + ACK + BusyBox
      "busybox": {
        "name": "{name0} + {fs.busybox.name}",
        "deps": [
          "dl.archive.ack.busybox"
        ],
        "includes": [
          "oc.uboot", "k.ack", "fs.busybox",
        ],
      },
    },

    ### Latest landing team kernel configs
    "latest": {
      "name0": "{k.latest.name}",

      ### U-Boot + latest-armlt + BusyBox
      "busybox": {
        "name": "{name0} + {fs.busybox.name}",
        "deps": [
          "dl.archive.latest.busybox"
        ],
        "includes": [
          "oc.uboot", "k.latest", "fs.busybox",
        ],

        ### EDK II UEFI + Mainline variant
        "edkii": {
          "name0": "{fw.edkii.name}",
          "deps": [
            "dl.archive.latest.busybox.edkii"
          ],
          "includes": [
            "fw.edkii", "k.mainline.pb", "fs.busybox",
          ],
        },
      },

      ### latest-armlt + OpenEmbedded
      "oe": {

        ### ALIP variant
        "alip": {
          "name": "{name0} + {fs.oe.alip.name}",
          "deps": [
            "dl.img.oe.alip", "dl.archive.latest.oe",
          ],
          "includes": [
            "oc.uboot", "k.latest", "fs.oe.alip",
          ],
        },

        ### Minimal variant
        "mini": {
          "name": "{name0} + {fs.oe.mini.name}",
          "deps": [
            "dl.img.oe.mini", "dl.archive.latest.oe",
          ],
          "includes": [
            "oc.uboot", "k.latest", "fs.oe.mini",
          ],

          ### Minimal Debug variant
          "debug": {
            "deps": [
              "dl.img.oe.mini",
              "dl.archive.latest.oe",
              "dl.archive.latest.oe.debug",
            ],
          },
        },

        ### LAMP variant
        "lamp": {
          "name": "{name0} + {fs.oe.lamp.name}",
          "deps": [
            "dl.img.oe.lamp", "dl.archive.latest.oe",
          ],
          "includes": [
            "oc.uboot", "k.latest", "fs.oe.lamp",
          ],

          ### LAMP Debug variant
          "debug": {
            "deps": [
              "dl.img.oe.lamp",
              "dl.archive.latest.oe",
              "dl.archive.latest.oe.debug",
            ],
          },
        },
      },
    },

    ### Standalone EDK II UEFI
    "edkii": {
      "name": "{fw.edkii.name}",
      "deps": [
        "dl.archive.edkii",
      ],
      "includes": [
        "fw.edkii",
      ],
    },
  },


  ###
   # Other software components that are included based on the user's choices.
  ###
  "oc": {

    ### Motherboard firmware
    "mb": {
      "name": "Motherboard firmware",
      "vsn": "null",
      "commit": "{@.mb.commit}",
      "priority": 0,
    },

    ### Trusted Firmware-A
    "tfa": {
      "name": "Trusted Firmware-A",
      "vsn": "2.0",
      "commit": "dbc8d9496e",
      "priority": 11,
    },

    ### SCP-Firmware
    "scp": {
      "name": "SCP-Firmware",
      "vsn": "2.4.0",
      "commit": "8533a3eeb7",
      "priority": 12,

      ### Juno variant of SCP firmware (closed source binary blob only)
      "juno": {
        "vsn": "1.28-rc0",
        "commit": "null",
      },
    },

    ### OP-TEE
    "optee": {
      "name": "OP-TEE {part}",
      "vsn": "3.0",

      "os": {
        "part": "OS",
        "commit": "94ee4938f7",
        "priority": 21,
      },

      "client": {
        "part": "Client",
        "commit": "09b69afa5e",
        "priority": 22,
      },
    },

    ### U-Boot
    "uboot": {
      "name": "{linaro.lt} U-Boot",
      "vsn": "2017.07",
      "commit": "1c62c1eaa1",
      "priority": 41,
    },
  },


  ###
   # Downloads.
   #
   # Each download must define:
   #
   #    name: file name
   #    url: base URL
   #    dir: destination directory, relative to <workspace> root
   #
   # Downloads may optionally define:
   #
   #    md5name: md5 checksum file name
   #
   # The actual URL used to download the file is "{url}/{name}". Similarly, the
   # actual URL used to download the md5 checksum file is "{url}/{md5name}".
  ###
  "dl": {

    ### Tools
    "tool": {

      ### Compilers
      "gcc": {
        "rel": "6.2-2016.11",
        "vsn": "6.2.1-2016.11",
        "url":"http://{linaro.url}/components/toolchain/binaries/{rel}/{tuple}",
        "name": "gcc-linaro-{vsn}-x86_64_{tuple}.tar.xz",
        "md5name": "{name}.asc",
        "dir": "tools/gcc",

        ### a32 compiler
        "a32": {
          "tuple": "arm-linux-gnueabihf",
        },

        ### a64 compiler
        "a64": {
          "tuple": "aarch64-linux-gnu",
        },

        ### SCP/MCP compilers (GNU-RM)
        "scp": {
          "tuple": "arm-none-eabi",
          "name": "gcc-{tuple}-{vsn}-linux.tar.bz2",

          ### GNU-RM 5
          "5": {
            "url": "https://launchpad.net/gcc-arm-embedded/{rel}/+download",
            "md5name": "{name}/+md5",
            "rel": "5.0/5-2016-q3-update",
            "vsn": "5_4-2016q3-20160926",
          },

          ### GNU-RM 7
          "7": {
            "url": "{arm.cms}/-/media/Files/downloads/gnu-rm/{rel}",
            "md5name": "null",
            "rel": "7-2018q2",
            "vsn": "7-2018-q2-update",
          },
        },
      },

      ### Repo
      "repo": {
        "name": "repo",
        "dir": "tools",
        "url": "storage.googleapis.com/git-repo-downloads",
      },
    },

    ### Images
    "img": {
      "dir": ".",  # Unless overridden, images are extracted to workspace <root>

      ### Android
      "android": {
        "url": "{@.android.url}",
        "md5name": "MD5SUMS",
        "rootfs": {
          "name": "{@.android.rootfs}",
        },
        "ramdisk": {
          "name": "ramdisk.img",
          "dir": "{@.android.ramdisk.dir}"
        },
      },

      ### OpenEmbedded
      "oe": {
        "url": "{@.oe.url}",
        "name": "{name0}-openembedded_{name1}-{name2}-gcc-{@.oe.vsn}.img.gz",

        ### ALIP image
        "alip": {
          "name0": "lsk-vexpress",
          "name1": "alip",
          "name2": "armv7a",
          "md5name":"null",
        },

        ### Minimal image
        "mini": {
          "name0": "lt-vexpress64",
          "name1": "minimal",
          "name2": "armv8",
          "md5name": "{@.oe.md5name}",
        },

        ### LAMP image
        "lamp": {
          "name0": "lt-vexpress64",
          "name1": "lamp",
          "name2": "armv8",
          "md5name": "{@.oe.md5name}",
        },
      },

      ### Fedora Server
      "fedora": {
        "url0": "https://dl.fedoraproject.org/pub/fedora-secondary/releases",
        "url": "{url0}/{@.fedora.rel}/Server/aarch64/iso/",
        "name": "Fedora-Server-dvd-aarch64-{@.fedora.vsn}.iso",
      },
    },

    ### Prebuilt archives
    "archive": {
      "url": "{@.pburl}",
      "md5name": "MD5SUMS",
      "dir": "{basename}",
      "name0": "{@.pdir}",
      "name3": "uboot",
      "basename": "{name0}-{name1}-{name2}-{name3}",
      "name": "{basename}.{fmt}",
      "fmt": "zip",

      ### ACK based archives
      "ack": {
        "name1": "ack",

        ### ACK + Android
        "android": {
          "name2": "{fs.android.script}",

          ### Debug variant
          "debug": {
            "name3": "debug",
            "fmt": "tar.xz",
          },
        },

        ### ACK + BusyBox
        "busybox": {
          "name2": "{fs.busybox.script}",
        },
      },

      ### latest-armlt based archives
      "latest": {
        "name1": "latest",

        ### U-Boot + latest-armlt + BusyBox
        "busybox": {
          "name2": "{fs.busybox.script}",

          ### Replace U-Boot with UEFI
          "edkii": {
            "name3": "{fw.edkii.stubfs}",
          },
        },

        ### latest-armlt + OE
        "oe": {
          "name2": "{fs.oe.script}",

          ### Debug variant
          "debug": {
            "name3": "debug",
            "fmt": "tar.xz",
          },
        },
      },

      ### Standalone EDK II UEFI archive
      "edkii": {
        "basename": "{name0}-{fw.edkii.stubfs}",
      },
    },
  },
}


"""
 " See documentation of ARMPLATDB.
"""
class Database(dict):
    def lookup( self, key, plat=None, noneAllowed=False ):
        log.debug(f"lookup {key} (plat={plat}, nA={noneAllowed})")
        def subPlat(k):
            try:
                return k.replace("@", plat) if "@" in k else k
            except TypeError:
                script.abort(f"lookup key={key} with invalid plat={plat}")
        # We assimilate keys into d at each level of lookup, allowing for keys
        # to be inherited and overridden by later levels.
        d = {}
        assimilate = lambda src: [d.update({k:v}) for k,v in src.items()]
        assimilate(self)
        # Perform recursive lookup
        (lookupLvl, item) = (d, None)
        for k in subPlat(key).split("."):
            item = lookupLvl[k] if k in lookupLvl else d[k] if k in d else None
            if item is None:
                break
            # Handle cross-references
            if isinstance(item, str) and item.startswith("#"):
                if item.count("{")==item.count("}")==1:
                    (l, r) = (item.find("{"), item.find("}"))
                    item = Database(d).lookup(item[l+1:r], plat)
            # Prepare for next level of lookup
            if isinstance(item, dict):
                item = Database(item)
                lookupLvl = item
                assimilate(lookupLvl)
        # String items have special behaviour
        if isinstance(item, str):
            log.debug(f"got {item}")
            if f"{{{key}}}" == item:
                script.abort("recursive lookup 111")
            if "null" == item:
                item = None
            elif "true" == item:
                item = True
            else:
                item = subPlat(item)
                if not item.count("{")==item.count("}"):
                    script.abort(f"lookup of key={key} with plat={plat} gives "
                                 f"item={item} with imbalanced number of {{ "
                                  "and }}")
                while "{" in item:
                    (l, r) = (item.find("{"), item.find("}"))
                    search, replace = item[l+1:r], item[l:r+1]
                    sub = Database(d).lookup(search, plat)
                    old_item = item
                    item = item.replace(replace, sub)
                    log.debug(f"substitution of {replace}='{sub}' in "
                              f"{old_item} gave {item}")
                    if item == old_item:
                        log.debug("detected possible recursion")
                        log.debug("attempting lookup again using root database")
                        sub = dblu(search, plat)
                        item = old_item.replace(replace, sub)
                        log.debug(f"resubstitution of {replace}='{sub}' in "
                                  f"{old_item} gave {item}")
                        if item == old_item:
                            script.abort("recursive lookup")
        if item is None and not noneAllowed:
            script.abort(f"lookup of {key} (plat={plat}) returns None but "
                          "noneAllowed is False")
        return item


    def multilookup( self, root, keys, plat=None, noneAllowed=False ):
        return [self.lookup(root+"."+k, plat, noneAllowed) for k in keys]


"""
 " Cast ARMPLATDB into an actual Database instance, then define shorthand
 " wrappers for lookup and multi-key lookup.
"""
ARMPLATDB = Database(ARMPLATDB)

def dblu( key, plat=None, noneAllowed=False ):
    return ARMPLATDB.lookup(key, plat, noneAllowed)

def dblum( root, keys, plat=None, noneAllowed=False ):
    return ARMPLATDB.multilookup(root, keys, plat, noneAllowed)


"""
 " Script initialisation and configuration.
"""
class script:
    def init():
        # Parse arguments
        p = argparse.ArgumentParser()
        p.add_argument("-v", help="verbose info", action="count", default=0)
        p.add_argument("--qa_mode", help="for Arm internal QA purposes",
                       action="store_true")
        p.add_argument("--no_check_apt_deps", action="store_true",
                       help="do not check for APT package dependencies")
        args = p.parse_args()
        (script.v, script.qa_mode, script.no_check_apt_deps) = \
            (args.v, args.qa_mode, args.no_check_apt_deps)
        # Configure logging
        logger = logging.getLogger(__name__)
        logger.setLevel(logging.DEBUG)
        console = logging.StreamHandler()
        # Logging level increases with number of -v on command line
        script.loglvl = {0:logging.WARNING, 1:logging.INFO, 2:logging.DEBUG} \
            [max(0, min(script.v, 2))]
        console.setLevel(script.loglvl)
        console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(console)
        # 2+ -v enables logging to file
        if script.v > 2:
            fh = logging.FileHandler("log.txt", "w")
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
            logger.addHandler(fh)
        log.info(f"Arm Platforms {dblu('arm.rel')} workspace initialization "
                 f"script {dblu('arm.vsn')}")
        log.info(f"date is {time.strftime('%Y-%m-%d %H:%M:%S')}")
        log.info(f"running on {HOST} host")
        # Other setup
        script.aborts = []
        sh.init()


    """
     " Start a QA session where we attempt to fetch every download and sync
     " every repo manifest defined in ARMPLATDB; this helps to identify URLs
     " that have have gone stale since the previous release / QA run.
    """
    def start_qa():
        script.qa_t0 = time.time()


    """
     " End a QA session, printing useful diagnostics.
    """
    def end_qa( hard_aborted=False ):
        t = time.time() - script.qa_t0
        hrs = math.floor(t / 3600)
        mins = math.floor((t % 3600) / 60)
        secs = math.floor((t % 3600) % 60)
        logfn = log.info if len(script.aborts)==0 else log.error
        msg = (f"QA run {'completed' if not hard_aborted else 'HARD ABORTED'} "
               f"after {hrs}hrs {mins}mins {secs}secs")
        wall = "#"*len(msg)
        logfn(wall)
        logfn(msg)
        logfn(f"Total number of aborts: {len(script.aborts)}")
        logfn(wall)
        logfn(f"QA result: {'SUCCESS' if len(script.aborts)==0 else 'FAIL'}")
        logfn(wall)
        return len(script.aborts)


    def abort( e=None, hard=True ):
        if e:
            log.error("")
            log.error(str(e))
        script.aborts.append(str(e) if e else "<<empty abort : see full log>>")
        do_exit = True
        if script.qa_mode:
            if hard:
                script.end_qa(hard_aborted=True)
            else:
                do_exit = False
        if do_exit:
            sys.exit(e.errno if hasattr(e, "errno") else -1)


"""
 " Convenience wrappers around logging operations.
"""
class log:
    def debug( msg ): logging.getLogger(__name__).debug(msg)
    def info ( msg ): logging.getLogger(__name__).info (msg)
    def warn ( msg ): logging.getLogger(__name__).warn (msg)
    def error( msg ): logging.getLogger(__name__).error(msg)


"""
 " Convenience wrappers around shell and file I/O operations.
"""
class sh:
    """
     " All file I/O operations are performed relative to <workspace> root; if
     " the script resident directory and the current working directory differ
     " then we need to user to clarify which they want to be their <workspace>.
    """
    def init():
        (srd, cwd) = (sh.fmtpath(sys.path[0])+"/", sh.fmtpath(os.getcwd())+"/")
        if HOST=="Linux":
            (srd, cwd) = ("/"+srd, "/"+cwd)
        sh.cwd = cwd if cwd==srd else \
            prompt("Current working dir differs from script resident dir.\n"
                   "## Please specify which to initialize as your workspace",
                   [
                     choice(cwd, descr="working", meta=cwd),
                     choice(srd, descr="script", meta=srd)
                   ]
            ).meta
        try:
            os.chdir(sh.cwd)
        except OSError as e:
            script.abort(e)
        sh.dld = sh.mkdir("downloads", hidden=True)+"/"
        sh.filename = __file__.rsplit("/" if HOST=="Linux" else "\\")[-1]
        sh.repod = ".repo/"
        check_empty_ws()


    """
     " Use unified '/' path delimiter regardless of host OS.
    """
    def fmtpath( p ):
        p = p.replace("\\", "/")
        while "//" in p:
            p = p.replace("//", "/")
        return p.strip().lstrip("/").rstrip("/")


    """
     " try/except wrapper around caller-provided func(), with optional logging
    """
    def _op( func, *paths, extra=None, silent=False ):
        paths = [sh.fmtpath(p) for p in paths]
        if not silent:
            log.info("{} {} {}".format(func, *paths, extra if extra else ''))
        try:
            return func(*paths) if not extra else func(*paths, extra)
        except OSError as e:
            script.abort(e)


    def mkdir( p, hidden=False ):
        if hidden:
            slash = p.rfind("/")
            p = f"{p[:slash+1]}.{p[slash+1:]}"
        if not sh._op(os.path.isdir, p, silent=True):
            sh._op(os.makedirs, p)
        if hidden and HOST=="Windows":
            ret = sh._op(ctypes.windll.kernel32.SetFileAttributesW, p,
                         extra=0x2) # 0x2 == FILE_ATTRIBUTE_HIDDEN
            if 0==ret:
                script.abort(ctypes.WinError())
        return p


    def rmdir( p ):
        if sh._op(os.path.isdir, p):
            sh._op(shutil.rmtree, p)


    def cp( src, dstdir ):
        sh._op(shutil.copy, src, dstdir)
        return f"{dstdir}/{src.split('/')[-1]}"


    def rm( p ):
        if sh._op(os.path.isfile, p):
            sh._op(os.remove, p)


    """
     " Extract an archive that has standardised extractall() function.
     " This applies to tar and zip.
    """
    def _std_extract( func, src, dstdir, *args, **kwargs ):
        (src, dstdir) = (sh.fmtpath(src), sh.fmtpath(dstdir))
        log.info(f"{func} {src} {dstdir}")
        try:
            with func(src, *args, **kwargs) as f:
                f.extractall(dstdir)
        except OSError as e:
            script.abort(e)
        return dstdir


    """
     " Extract an archive that can be treated as a binary stream.
     " This applies to gzip and bz2.
    """
    def _bin_extract( func, src, dstdir, extn ):
        (src, dstdir) = (sh.fmtpath(src), sh.fmtpath(dstdir))
        dst = f"{dstdir}/{src.split('/')[-1][:-len(extn)]}"
        log.info(f"{func} {src} {dst}")
        try:
            with func(src, "rb") as inf:
                with open(dst, "wb") as outf:
                    while True:
                        chunk = inf.read(0x1000)
                        if not chunk:
                            break
                        outf.write(chunk)
        except OSError as e:
            script.abort(e)
        return dst


    def _tarxf( src, dstdir ):
        return sh._std_extract(tarfile.open, src, dstdir, "r:xz", errorlevel=1)


    def _tarxjf( src, dstdir ):
        return sh._std_extract(tarfile.open, src, dstdir, "r:bz2", errorlevel=1)


    def _unzip( src, dstdir ):
        return sh._std_extract(zipfile.ZipFile, src, dstdir, "r")


    def _gunzip( src, dstdir ):
        return sh._bin_extract(gzip.open, src, dstdir, ".gz")


    def _bunzip2( src, dstdir ):
        return sh._bin_extract(bz2.BZ2File, src, dstdir, ".bz2")


    """
     " Extract (if an archive) or copy (if not an archive) a downloaded file
     " from the hidden .downloads/ dir to the file's intended destination dir.
    """
    def extract_or_copy( src, dstdir ):
        sh.mkdir(dstdir)
        ends = lambda s: src.endswith(s)
        handler = sh._tarxf   if ends(".tar.xz") else \
                  sh._tarxjf  if ends(".tar.bz2") else \
                  sh._unzip   if ends(".zip") else \
                  sh._gunzip  if ends(".gz") and not ends(".tar.gz") else \
                  sh._bunzip2 if ends(".bz2") and not ends(".tar.bz2") else \
                  sh.cp
        return handler(src, dstdir)


    """
     " Generate an MD5 checksum for a file.
    """
    def md5sum( p ):
        p = sh.fmtpath(p)
        log.info("md5sum "+p)
        md5 = hashlib.md5()
        try:
            with open(p, "rb") as f:
                while True:
                    block = f.read(md5.block_size)
                    if not block:
                        break
                    md5.update(block)
        except OSError as e:
            script.abort(e)
        md5 = md5.hexdigest()
        log.info("got hash "+md5)
        return md5


    """
     " Check whether a file's actual MD5 checksum matches what is specified in
     " an MD5 checksum file.
    """
    def md5check( p, sumsp ):
        (p, sumsp) = (sh.fmtpath(p), sh.fmtpath(sumsp))
        (md5, name) = (sh.md5sum(p), p.split("/")[-1])
        log.debug(f"checking md5 of {name} in {sumsp}")
        try:
            with open(sumsp, "r") as sumsf:
                rstr = re.compile(r"([^\s]+)\s+"+re.escape(name))
                regex = re.search(rstr, sumsf.read())
        except OSError as e:
            script.abort(e)
        if not regex:
            script.abort(f"no md5 for {name} in sumsfile {sumsp}", hard=False)
            return False
        match = "MATCH" if (regex.group(1) == md5) else "MISMATCH"
        log.info(f"md5 {match} {regex.group(1)}")
        return (regex.group(1) == md5)


    """
     " Downloads are saved to a subdir of the hidden .downloads/ dir based on
     " the URL from which they were downloaded.
    """
    def url2dld( url ):
        p = url.lstrip("/").rstrip("/").replace(":","_").replace("/","_")
        while "__" in p:
            p = p.replace("__", "_")
        return f"{sh.dld}/{p}"


    """
     " Download a file from a URL with optional pretty-printed progress.
    """
    def wget( url, name, silent=False ):
        def si( num_bytes ):
            if num_bytes <= 0:
                return "0 B"
            suffixes = ["B", "KiB", "MiB", "GiB"]
            log = int(math.floor(math.log(num_bytes, 1024)))
            size = round(num_bytes / 1024**log, 2)
            return "{:.2f} {}".format(size, suffixes[log])
        dld = sh.url2dld(url)
        url = f"{url}/{name}"
        log.info("wget "+url)
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://"+url
            log.debug("url does not specify protocol, defaulting to https")
        try:
            r = urllib.request.urlopen(url)
            log.debug("connected to server")
            try:
                endln = " / "+si(int(r.getheader("Content-Length").strip()))
            except ValueError():
                endln = ""
                log.debug("server does not support Content-Length header")
                log.debug("file has unknown length")
            sh.mkdir(dld)
            dst = f"{dld}/{name.split('/')[-1]}"
            log.info(f"opening dst file {dst}")
            with open(dst, "wb") as f:
                progress = 0
                eraseln = f"\r{' '*(shutil.get_terminal_size().columns-1)}\r"
                while True:
                    chunk = r.read(0x100000)
                    if not chunk:
                        break
                    f.write(chunk)
                    progress += len(chunk)
                    if not silent:
                        print(f"{eraseln}Fetch {name}: {si(progress)}{endln}",
                            end="")
                    sys.stdout.flush()
                if not silent:
                    print() ### for \n\r
            log.info("successfully fetched "+si(progress))
            return True
        except (OSError, urllib.error.HTTPError) as e:
            script.abort(e, hard=False)
            return False


    """
     " Convenience around wget() that checks whether a file has already been
     " successfully downloaded and, if yes, skips the download this time. This
     " is only possible for downloads that define an MD5 checksum file as we
     " cannot just rely on a file with the correct name being present. Simiarly
     "
    """
    def fetch( key, plat=None, force_fresh=False ):
        (url, name, md5name, dstdir) =  \
            dblum(key, ["url","name","md5name","dir"], plat, noneAllowed=True)
        dld = sh.url2dld(url)
        dlfile = f"{dld}/{name.split('/')[-1]}"
        if md5name:
            md5file = f"{dld}/{md5name.split('/')[-1]}"
            sh.wget(url, md5name, silent=True)
        already_fetched = False
        if sh._op(os.path.isfile, dlfile, silent=True):
            if md5name and sh._op(os.path.isfile, md5file, silent=True):
                if not force_fresh and sh.md5check(dlfile, md5file):
                    already_fetched = True
                    print("Already fetched "+name)
                    sys.stdout.flush()
        if not already_fetched:
            sh.wget(url, name)
            if md5name and sh._op(os.path.isfile, md5file, silent=True):
                if not sh.md5check(dlfile, md5file):
                    script.abort((f"MD5 mismatch after fetching {dlfile} "
                                  f"(sumf {md5file})"), hard=False)
        print(f"Extracting {name}...")
        return sh.extract_or_copy(dlfile, dstdir)


    """
     " Sync a repo manifest.
    """
    def reposync( manifest, p, force_fresh=False ):
        log.info("Attempting to sync "+manifest)
        if force_fresh:
            sh.rmdir(".repo")
        repo = sh.fetch("dl.tool.repo")
        def call_repo(argstr):
            sp = subprocess
            log.debug(f"calling {['unbuffer', 'python2', repo] + argstr.split(' ')}")
            proc = sp.Popen(["unbuffer", "python2", repo] + argstr.split(" "),
                            stdout=sp.PIPE, stderr=sp.STDOUT, bufsize=0,
                            universal_newlines=True)
            ln = ""
            while proc.returncode is None:
                ln += os.read(proc.stdout.fileno(), 1).decode("utf-8")
                if ln.endswith("\r") or ln.endswith("\n"):
                    log.info(ln.rstrip("\n"))
                    ln = ""
                proc.poll()
            return proc.returncode
        def init():
            print("\nInitialising repo")
            if not 0==call_repo(f"init -u {dblu('@.manifests', p)} "
                                f"-b {dblu('@.mrel', p)} -m {manifest}"):
                script.abort("failed to initialise repo")
        def sync():
            print("Syncing repo")
            print("NOTE: this can take a very long time, please be patient")
            print("NOTE: run script with -v for detailed repo progress info")
            if not 0==call_repo("sync -j8 --force-sync"):
                script.abort("failed to sync repo", hard=False)
        init()
        sync()
        hooks = dblu("@.pihooks", p, noneAllowed=True)
        if hooks:
            [pihooks.__dict__[h]() for h in hooks]


    """
     " Call an external program, optionally piping the program's stderr to its
     " stdout, and optionally piping the program's stdout to the script's.
    """
    def call( args, err2out=False, pipe2sh=False ):
        log.info(f"calling {' '.join(args)}")
        sp = subprocess
        stdout = None if pipe2sh else sp.PIPE
        stderr = sp.STDOUT if err2out else None if pipe2sh else sp.PIPE
        with sp.Popen(args, stdout=stdout, stderr=stderr) as p:
            (out, err) = p.communicate()
        strip = lambda s: s.decode("utf-8").strip() if s else ""
        return (p.returncode, strip(out), None if err2out else strip(err))


"""
 " Post-initialisation hooks that may be referenced by platforms in ARMPLATDB.
"""
class pihooks():
    pass


"""
 " Class representing a choice in a menu.
"""
class choice():
    def __init__( self, name, meta=None, descr=None, disabled=None,
                  children=None ):
        (self.name, self.meta, self.descr, self.disabled, self.children) = \
            (name, meta, descr, disabled, [])
        if children:
            [self.add(c) for c in children]


    """
     " Add a child node to this choice; see tree_prompt().
    """
    def add( self, child ):
        log.debug("append child "+child.meta)
        self.children.append(child)


    """
     " Generate a tree structure of choices; see tree_prompt().
    """
    def tree( keylist, root, plat=None, gen_root=True ):
        if gen_root:
            root = choice("<root>", meta=root)
        log.debug(f"tree({root.meta})")
        keylist = list(filter(lambda k: k.startswith(root.meta), keylist))
        if len(keylist)==1 and keylist[0]==root.meta:
            log.debug(f"{root.meta} is leaf node")
            return None
        keys = []
        for k in keylist:
            log.debug(">>>> "+k)
            key = k[len(root.meta)+1:]
            if "." in key:
                key = key[:key.find(".")]
            keys.append(key)
        keys = [f"{root.meta}.{k}" for k in sorted(list(set(keys)))]
        if len(keys) > 0:
            log.debug("generating child nodes")
            for k in keys:
                log.debug("k="+k)
                root.add(choice(
                    dblu(k+".name", plat), meta=k,
                    descr=dblu(k+".descr", plat, noneAllowed=True)))
            log.debug("crawling child nodes")
            for c in root.children:
                choice.tree(
                    keylist,root.children[root.children.index(c)],plat,False)
        return root


"""
 " Prompt the user to make a selection from a list of choices.
"""
def prompt( title, choices ):
    pad = 3 + max([len(c.name) for c in choices])
    def fmt_choice( c ):
        descr = "-- <DISABLED>" if c.disabled else \
                f"-- {c.descr}" if c.descr else ""
        return "{:>2}) {:{}}{}".format(choices.index(c)+1, c.name, pad, descr)
    msg = "\n".join(map(fmt_choice, choices))
    msg = f"\n\n## {title}:\n\n{msg}\n\n> "
    for ln in msg.splitlines():
        if not ln.startswith(">"):
            log.debug(ln)
    while True:
        try:
            i = int(input(msg))
            if (i < 1) or (i > len(choices)):
                raise ValueError()
            i = i - 1
            if choices[i].disabled:
                print(f"Not available: {choices[i].descr}")
            else:
                break
        except ValueError:
            print(f"Expected number in range [1..{len(choices)}] inclusive.\n")
    log.debug(f"> {i+1} (meta={choices[i].meta})")
    return choices[i]


"""
 " Prompt the user to make a select from a tree structure of choices; if the
 " chosen choice has more than one child node, prompt the user to also make a
 " selection from those child nodes (if there is only one child node, that child
 " node is automatically selected). Keep going until we hit a leaf node.
"""
def tree_prompt( title, root ):
    log.debug("tree_prompt "+title)
    log.debug(f"root has {len(root.children)} children")
    while root.children:
        if len(root.children)==1:
            root = root.children[0]
            log.warn("only avail. option is "+root.name)
        else:
            root = prompt(title, root.children)
    return root


"""
 " Check whether host Linux system (with APT based package manager) has all
 " required packages installed. TODO: Add support for DNF/YUM. If you're
 " using a Linux system with some other (or no) package manager, make sure all
 " equivalents to all required programs are installed and then re-run the
 " script with --no_check_apt_deps.
"""
def check_apt_deps():
    if script.no_check_apt_deps:
        log.debug("skipping APT dependency check")
        return
    try:
        import apt
    except:
        log.error("cannot check package dependencies (failed to import apt)")
        log.error("the following apt packages are required:")
        log.error("")
        [log.error(" "+d) for d in dblu("host.linux.pkgs.apt")]
        log.error("")
        log.error("please install python3-apt or check package dependencies")
        log.error("manually and re-run the script with `--no_check_apt_deps'")
        script.abort()
    log.debug("checking APT package dependencies")
    cache = apt.Cache()
    missing = filter(lambda d: not cache[d].is_installed,
        dblu("host.linux.pkgs.apt"))
    missing = list(missing)
    if missing:
        log.error("the following apt packages are missing:")
        log.error("")
        [log.error(" "+d) for d in missing]
        log.error("")
        log.error("please install these packages using:")
        log.error("")
        log.error(f"sudo apt-get install {' '.join(missing)}")
        script.abort()


"""
 " Building some host tools breaks with a host gcc that is too old or too new.
"""
def check_sys_gcc():
    log.debug("checking system gcc")
    (_, out, _) = sh.call(["gcc", "-dumpversion"])
    ver = tuple([int(i) for i in out.split(".")])
    if ver < (5,4,0) or ver >= (8,0,0):
        script.abort(f"detected system native gcc version {ver[0]}.{ver[1]}"
                     f".{ver[2]}, please use a version later than 5.4 and "
                      "earlier than 8.0")


"""
 " Git must be sufficiently configured for repo to work.
"""
def check_git_config():
    log.debug("checking git config")
    (_, cfg, _)  = sh.call(["git", "config", "-l"])
    for setting in ["user.name", "user.email", "color.diff"]:
        if not setting in cfg:
            log.error("git is not correctly configured")
            log.error("please ensure the following git configs are set:")
            log.error("")
            log.error("git config --global user.name \"Joe Bloggs\"")
            log.error("git config --global user.email \"jb@example.com\"")
            log.error("git config --global color.diff \"auto\"")
            script.abort()


"""
 " Generate a list of all files and folders in <workspace>.
"""
def get_ws_files():
    filelist = []
    for (_, dirs, files) in os.walk(sh.cwd):
        dirs = [d+"/" for d in dirs]
        filelist += sorted(files + dirs)
        break  # don't recursively walk
    log.debug("found the following files in workspace directory:")
    log.debug(filelist)
    for f in [sh.filename, sh.dld, sh.repod, "log.txt"]:
        if f in filelist:
            filelist.remove(f)
    return filelist


"""
 " Check whether <workspace> is empty and, if not, confirm with user whether
 " it's OK to delete these files/folders and proceed.
"""
def check_empty_ws():
    filelist = get_ws_files()
    if len(filelist) > 0:
        lst = "\n - ".join(filelist)
        msg = (f"Expected empty workspace but found these files & folders:\n\n "
               f"{lst}\n\n## Delete these files & folders and proceed?")
        if not prompt(msg, [choice("Yes", True), choice("No", False)]).meta:
            sys.exit(0)
        [sh.rmdir(f) if f.endswith("/") else sh.rm(f) for f in filelist]


"""
 " Decide which software configuration will be downloaded.
"""
class config:
    def query():
        while not config._choose():
            pass


    def sync():
        print("\nFetching and extracting dependencies...\n")
        for d in config.deps:
            sh.fetch(d, plat=config.p.meta)
        if config.manifest:
            force_fresh = False
            while True:
                sh.reposync( config.manifest, config.p.meta, force_fresh )
                for (_, dirs, _) in os.walk("build-scripts/platforms"):
                    break  # don't recursively walk
                for (_, _, files) in os.walk("build-scripts/filesystems"):
                    break  # don't recursively walk
                if dirs and files:
                    break
                else:
                    log.error("detected missing files/folders in build-scripts/")
                    log.error("repo may be in an invalid/corrupt state")
                    log.error("did you lose internet connection during sync?")
                    msg = ("Detected potential invalid/corrupt repo state, try "
                           "to sync again?")
                    if not prompt(msg,
                        [choice("Yes", True), choice("No", False)]
                    ).meta:
                        sys.exit(0)
                    force_fresh = True
                    filelist = get_ws_files()
                    [sh.rmdir(f) if f.endswith("/") else sh.rm(f) for f in filelist]
            for d in dirs:
                if not (d=="common" or d==dblu(config.p.meta+".pdir")):
                    sh.rmdir("build-scripts/platforms/"+d)
            preserve = dblu(config.fs.meta+".script") if config.env.meta=="k" \
                  else dblu(config.fw.meta+".stubfs")
            for f in files:
                if not f==preserve:
                    sh.rm("build-scripts/filesystems/"+f)
        print("\nWorkspace initialised.")
        if config.ws.meta=="bfs":
            print("\nTo build:\n")
            print("    chmod a+x <workspace>/build-scripts/build-all.sh")
            print("    <workspace>/build-scripts/build-all.sh all")
            print("\nResulting binaries will be placed in:")
            print("\n    <workspace>/output/{}-{}/".format(
                      dblu("@.pdir", config.p.meta),
                      dblu(config.fs.meta+".script" if config.env.meta=="k" \
                          else config.fw.meta+".stubfs", config.p.meta)))
        print("\nFor more information, tutorials, FAQs, and discussions, see:")
        print("\n    https://www.community.arm.com/tools/dev-platforms/")
        print("\nThank you for using the Arm Platforms workspace script.")


    def _choose():
        config.cfg = []
        config.deps = []
        config.swcs = []
        config.manifest = None
        config._add_cfg("Workspace", sh.cwd)
        config._choose_p()
        config._choose_ws()
        if config.ws.meta=="bfs":
            check_apt_deps()
            check_git_config()
            check_sys_gcc()
            config._add_deps(config.p.meta)
            config._choose_env()
            if config.env.meta=="k":
                config._choose_k()
                config._choose_fs()
            elif config.env.meta=="fw":
                config._choose_fw()
        elif config.ws.meta=="pbc":
            config._choose_pb()
        msg = "Your chosen configuration is shown below:\n"
        pad = [max([len(row[n]) for row in config.cfg]) for n in [0,1]]
        wall = "\n    +-"+(pad[0]*"-")+"-+-"+(pad[1]*"-")+"-+"
        msg += wall
        for row in config.cfg:
            msg += "\n    | {:<{:}} | {:<{:}} |" \
                   .format(row[0], pad[0], row[1], pad[1])
        msg += wall
        if config.swcs:
            config.swcs = sorted(list(set(config.swcs)),
                        key = lambda swc: dblu(swc+".priority", config.p.meta))
            rows = []
            for swc in config.swcs:
                (name, vsn, commit) = dblum(swc, ["name", "vsn", "commit"],
                                           config.p.meta, noneAllowed=True)
                descr = f"{vsn} " if vsn else ""
                descr += f"commit {commit}" if commit else ""
                rows.append([name, descr])
            pad = [max([len(r[n]) for r in rows]) for n in [0,1]]
            wall = "\n    +-"+(pad[0]*"-")+"-+-"+(pad[1]*"-")+"-+"
            msg += "\n\nThe following software components are included:\n"
            msg += wall
            for r in rows:
                msg += "\n    | {:<{:}} | {:<{:}} |" \
                       .format(r[0], pad[0], r[1], pad[1])
            msg += wall

        msg += "\n\n## Proceed with this configuration?"
        return prompt(msg, [choice("Yes", True), choice("No", False)]).meta


    def _add_cfg( key, val ):
        config.cfg.append((key, val))


    def _add_deps(key):
        deps = dblu(key+".deps", config.p.meta, noneAllowed=True)
        if deps:
            for d in deps:
                config.deps.append(d)


    def _add_swc(key):
        config.swcs.append(key)
        config._add_includes(key)


    def _add_includes(key):
        swcs = dblu(key+".includes", config.p.meta, noneAllowed=True)
        if swcs:
            for swc in swcs:
                config.swcs.append(swc)
                config._add_includes(swc)


    def _choose_p():
        log.debug("Building platform choice tree")
        def crawl(root):
            log.debug(f"crawl({root.meta})")
            num_en_children = 0
            if root.children:
                log.debug(f"{root.meta} is non-leaf")
                [crawl(c) for c in root.children]
                num_en_children = sum([not p.disabled for p in root.children])
                log.debug(f"{root.meta} has {num_en_children} enabled children")
            pbs = dblu(root.meta+".pb", noneAllowed=True)
            num_pbs = len(pbs) if pbs else 0
            if not root.children:
                log.debug(f"{root.meta} has {num_pbs} prebuilts")
            can_init_ws = True
            if num_pbs==0 and not HOST=="Linux":
                can_init_ws = False
                if not root.children:
                    log.debug("not running on Linux")
            log.debug(f"can_init_ws {root.meta}? {can_init_ws}")
            if (root.children and num_en_children==0) or not can_init_ws:
                log.debug(f"disabling node {root.meta}")
                root.disabled = True
                root.descr = "can only build from source for this platform, " \
                             "which requires a Linux host PC"
        ptree = choice.tree(dblu("p.all"), root="p")
        crawl(ptree)
        config.p = tree_prompt("Please select a platform", ptree)
        config._add_cfg("Platform", config.p.name)
        config._add_includes(config.p.meta)


    def _choose_ws():
        (ks,fws,pbs) = dblum(config.p.meta,["k","fw","pb"],config.p.meta,True)
        root = choice("<ws>", meta="ws")
        if (ks or fws) and HOST=="Linux":
            root.add(choice("Build from source", meta="bfs"))
        if pbs:
            root.add(choice("Use prebuilt configuration", meta="pbc"))
        config.ws = tree_prompt("Please specify whether you want to", root)
        config._add_cfg("Type", config.ws.name)


    def _choose_env():
        (ks, fws) = dblum(config.p.meta, ["k","fw"], config.p.meta, True)
        root = choice("<env>", meta="env")
        if ks:
            root.add(choice(dblu("k.name"), meta="k"))
        if fws:
            root.add(choice(dblu("fw.name"), meta="fw"))
        config.env = tree_prompt("Please select an environment", root)


    def _choose_fw():
        p = config.p.meta
        fwtree = choice.tree(dblu(p+".fw", p), "fw", p)
        config.fw = tree_prompt("Please select your firmware", fwtree)
        config._add_cfg("Configuration", config.fw.name)
        config._add_deps(config.fw.meta)
        config._add_swc(config.fw.meta)
        config._add_includes(config.fw.meta)
        config.manifest = dblu(config.fw.meta+".manifest", config.p.meta)


    def _choose_k():
        p = config.p.meta
        ktree = choice.tree(dblu(p+".k", p), "k", p)
        config.k = tree_prompt("Please select your kernel", ktree)
        config._add_deps(config.k.meta)
        config._add_swc(config.k.meta)
        config._add_includes(config.k.meta)
        config.manifest = dblu(config.k.meta+".manifest", config.p.meta)


    def _choose_fs():
        plat_fss = dblu(config.p.meta+".fs", config.p.meta)
        kernel_fss = dblu(config.k.meta+".fs", config.p.meta)
        fss = [fs for fs in kernel_fss if fs in plat_fss]
        fstree = choice.tree(fss, "fs", config.p.meta)
        config.fs = tree_prompt("Please select your filesystem", fstree)
        config._add_cfg("Configuration", f"{config.k.name} + {config.fs.name}")
        config._add_deps(config.fs.meta)
        config._add_swc(config.fs.meta)
        config._add_includes(config.fs.meta)


    def _choose_pb():
        choices = []
        for pb in dblu(config.p.meta+".pb", config.p.meta):
            choices.append(choice(dblu(pb+".name", config.p.meta), pb))
        if len(choices)==1:
            config.pb = choices[0]
            log.warn("only avail. option is "+config.pb.name)
        else:
            choices.append(choice("<< all >>", meta="all"))
            config.pb = prompt("Please select a configuration", choices)
        config._add_cfg("Configuration", config.pb.name)
        if config.pb.meta=="all":
            all_pb_deps = []
            for pb in dblu(config.p.meta+".pb", config.p.meta):
                pb_deps = dblu(pb+".deps", config.p.meta, noneAllowed=True)
                if pb_deps:
                    for d in pb_deps:
                        all_pb_deps.append(d)
            config.deps += list(set(all_pb_deps))
            config.swcs = []
        else:
            config._add_deps(config.pb.meta)
            config._add_includes(config.pb.meta)


def run():
    config.query()
    config.sync()
    return 0


def run_qa():
    check_empty_ws()
    script.start_qa()
    manifests = []
    for p in dblu("p.all"):
        log.info(">>> Running QA for platform "+p)
        (ks,fws,fss,pbs) = dblum(p,["k","fw","fs","pb"], p, noneAllowed=True)
        if ks:
            [manifests.append(dblu(k+".manifest", plat=p)) for k in ks]
        if fws:
            [manifests.append(dblu(fw+".manifest", plat=p)) for fw in fws]
        keys = (fss if fss else []) + (pbs if pbs else [])
        if keys:
            for k in keys:
                deps = dblu(k+".deps", plat=p, noneAllowed=True)
                if deps:
                    [sh.fetch(d, plat=p, force_fresh=True) for d in deps]
    for m in list(set(manifests)):
        log.info(">>> Attempting to sync manifest "+m)
        sh.reposync(m, "p", force_fresh=True)
    return script.end_qa()


if __name__ == "__main__":
    script.init()
    exit(run() if not script.qa_mode else run_qa())


