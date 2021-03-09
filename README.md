# Efficent_Process_Isolation_Domain_Level_AArch64
EFFICIENT DOMAIN-LEVEL PROCESS ISOLATION ON AARCH64​


## Getting Started
### Prerequisites

#### Dependencies
```
$ sudo apt-get install libxml2-dev genext2fs android-tools-adb tree python-dev gcc-multilib python bridge-utils python-numpy sshpass python-wand libxml2-utils libfreetype6-dev python-pip g++-multilib screen python-wrapt python-nose libncurses5:i386 cython gperf libstdc++6:i386 acpica-tools python-matplotlib android-tools-fastboot python-mako trace-cmd libc6:i386 kernelshark

$ sudo apt-get install chrpath gawk texinfo libssl-dev diffstat wget git-core unzip gcc-multilib \
 build-essential socat cpio python python3 python3-pip python3-pexpect xz-utils debianutils \
 iputils-ping python3-git python3-jinja2 libegl1-mesa libsdl1.2-dev pylint3 xterm git-lfs openssl \
 curl libncurses-dev libz-dev python-pip repo

$ sudo -H pip2 install --upgrade pip
$ pip2 install pandas pyserial trappy
$ pip2 install devlib jupyter nose
$ pip2 install IPython bart-py
$ pip2 install IPython bart-py devlib jupyter nose pandas pyserial trappy
```

#### [Arm DS](https://developer.arm.com/tools-and-software/embedded/arm-development-studio)

***

### Installation

#### [FVP, Fixed Virtual Platforms](https://developer.arm.com/tools-and-software/simulation-models/fixed-virtual-platforms)

```
$ repo init -u https://git.linaro.org/landing-teams/working/arm/arm-reference-platforms-manifest.git -m fvp-yocto.xml -b refs/tags/BASEFVP-2020.08.06
$ repo sync -j 4
```


```
$ git clone https://github.com/KKimj/DomainProcessIsolation-AArch64/

$ cd DomainProcessIsolation-AArch64
$ cd arm-reference-platforms
$ sudo python3 5518.armplat_1901.py
```

```
## Verified on Ubuntu 16.04 LTS, 18.04 LTS
## Model: FVP_Base_AEMv8A_revC version 11.8

$ cd arm-reference-platforms

$ python3 5518.armplat_1901.py
-> Answer 2 2 1 1 1 1 2 2 2 1

## Please select a platform:
 1) Development boards
 2) Fixed Virtual Platforms (FVPs)
> 2

## Please select a platform:
 1) System Guidance
 2) Armv8 architecture
> 2

## Please select a platform:
 1) Armv8-A Base Platform      -- 11.3.30+ (Rev C)
 2) Armv8-A Foundation Model   -- 11.3.30+
> 1

## Please select a platform:
 1) Armv8-A Base Platform with 64-bit software stack
 2) Armv8-A Base Platform with legacy 32-bit software stack
> 1

## Please specify whether you want to:
 1) Build from source
 2) Use prebuilt configuration
> 1

## Please select an environment:
 1) Linux kernel & userspace filesystem
 2) Firmware & test suites
> 1

## Please select your kernel:
 1) Linaro/ArmLT Android Common Kernel
 2) Linaro/ArmLT Latest Stable Kernel
> 2

## Please select your filesystem:
 1) BusyBox
 2) OpenEmbedded
> 2

## Please select your filesystem:
 1) OpenEmbedded LAMP
 2) OpenEmbedded Minimal
> 2

## Your chosen configuration is shown below:
    +---------------+----------------------------------------------------------+
    | Workspace     | /home/tosois01/linaro/19.01_9p/                          |
    | Platform      | Armv8-A Base Platform with 64-bit software stack         |
    | Type          | Build from source                                        |
    | Configuration | Linaro/ArmLT Latest Stable Kernel + OpenEmbedded Minimal |
    +---------------+----------------------------------------------------------+

The following software components are included:
    +-----------------------------------+-------------------------+
    | Trusted Firmware-A                | 2.0 commit dbc8d9496e   |
    | OP-TEE OS                         | 3.0 commit 94ee4938f7   |
    | OP-TEE Client                     | 3.0 commit 09b69afa5e   |
    | Linaro/ArmLT Latest Stable Kernel | 4.19 commit e97e8d868a  |
    | OpenEmbedded Minimal              | 15.09 4.9_20150912-729  |
    +-----------------------------------+-------------------------+

## Proceed with this configuration?:
 1) Yes
 2) No
> 1

$ ./patch_9p.sh
patching file linux/arch/arm64/boot/dts/arm/fvp-base.dtsi
patching file linux/linaro/configs/linaro-base.conf
patching file model-scripts/run_model.sh
patching file linux/linaro/configs/vexpress64.conf
patching file u-boot/include/configs/vexpress_aemv8a.h

$ ./build-scripts/build-all.sh all

# edit run.FVP_Base_AEMv8A_revC_118.sh

$ ./run.FVP_Base_AEMv8A_revC_118.sh

################################################################################

root@genericarmv8:~# dmesg | grep 9p
[    2.213610] 9p: Installing v9fs 9p2000 file system support
[    3.119498] 9pnet: Installing 9P2000 support

root@genericarmv8:~# mount -t 9p -o trans=virtio,version=9p2000.L FM /mnt

root@genericarmv8:~# ls -l /mnt/
total 8
drwxrwxrwx    2 root     root          4096 Jul 12 12:28 test

## Access directory must have permission 777 (rwxrwxrwx) so that GuestOS can write

root@genericarmv8:~# echo AAA > /mnt/test/a
root@genericarmv8:~# cat /mnt/test/a
AAA

## echo BBB > /mnt/test/a on HostOS

root@genericarmv8:~# cat /mnt/test/a
BBB
```

#### Installation Screen, It takes very very long time
![It takes very very long time](screenshots/screenshot4.PNG?raw=true "Title")




## Examples

![Alt text](screenshots/screenshot1.PNG?raw=true "Title")


![Alt text](screenshots/screenshot2.PNG?raw=true "Title")

```c
// exec.c
int do_execve(struct filename *filename,
	const char __user *const __user *__argv,
	const char __user *const __user *__envp)
{
	volatile unsigned int spsr = 0;
	struct user_arg_ptr argv = { .ptr.native = __argv };
	struct user_arg_ptr envp = { .ptr.native = __envp };
/*
...
*/
	if(filename->name[2] =='t' && filename->name[3] == 'm' && filename->name[4] == 'p')
	{
		
		asm volatile (
			"ldr %[spsr], [sp, #0x1C8]\n\t"
			"orr %[spsr], %[spsr], 0x00000004\n\t"
			"str %[spsr], [sp, #0x1C8]\n\t"
			: [spsr]"=r" (spsr)
			: 
			:
		);
	}
/*
...
*/
}
```

![Alt text](screenshots/screenshot3.PNG?raw=true "Title")




## References
- How to Install & Setup FVP
  - https://community.arm.com/developer/tools-software/oss-platforms/f/dev-platforms-forum/44264/how-to-enable-share-folders-between-fvp-and-host-linux
  - ~~https://git.linaro.org/landing-teams/working/arm/arm-reference-platforms.git/about/docs/basefvp/user-guide.rst~~
- git repository, Arm Reference Platforms
  - https://git.linaro.org/landing-teams/working/arm/arm-reference-platforms.git/ 
- How to setup Arm DS for Debugging
  - https://community.arm.com/developer/tools-software/tools/b/tools-software-ides-blog/posts/debugging-the-armv8-a-linux-kernel-with-ds-5 
  - https://community.arm.com/developer/tools-software/oss-platforms/f/dev-platforms-forum/48420/warning-dts3-nal2-fvp-linux-kernel-debug
- Linux Kernel code
  - https://elixir.bootlin.com/linux/v4.20.17/source/arch/arm64/kernel/process.c
- _do_fork()
  - http://egloos.zum.com/rousalome/v/9989596  
- Tread info
  - http://egloos.zum.com/rousalome/v/10012047
- PSTATE Register
  - https://sonseungha.tistory.com/464 
- Blog about AArch64
  - [https://gongpd.tistory.com/category/하드웨어 스케치북](https://gongpd.tistory.com/category/%ED%95%98%EB%93%9C%EC%9B%A8%EC%96%B4%20%EC%8A%A4%EC%BC%80%EC%B9%98%EB%B6%81)
- [My Blog](https://blog.naver.com/PostList.nhn?blogId=ziun99&from=postList&categoryNo=88)
