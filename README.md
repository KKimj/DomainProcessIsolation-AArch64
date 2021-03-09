# Efficent_Process_Isolation_Domain_Level_AArch64
EFFICIENT DOMAIN-LEVEL PROCESS ISOLATION ON AARCH64​


## Getting Started
### Prerequisites

#### Dependencies
```
$ sudo apt-get install libxml2-dev genext2fs android-tools-adb tree python-dev gcc-multilib python bridge-utils python-numpy sshpass python-wand libxml2-utils libfreetype6-dev python-pip g++-multilib screen python-wrapt python-nose libncurses5:i386 cython gperf libstdc++6:i386 acpica-tools python-matplotlib android-tools-fastboot python-mako trace-cmd libc6:i386 kernelshark

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
$ git clone https://github.com/KKimj/DomainProcessIsolation-AArch64/


$ git clone https://git.linaro.org/landing-teams/working/arm/arm-reference-platforms.git
$ cd arm-reference-platforms
$ sudo python3 sync_workspace.py
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
  - ~~https://git.linaro.org/landing-teams/working/arm/arm-reference-platforms.git/about/docs/user-guide.rst~~
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
