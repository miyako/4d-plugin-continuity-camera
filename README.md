# 4d-plugin-continuity-camera
Invoke macOS 10.14 Continuity Camera feature for image import

![version](https://img.shields.io/badge/version-19%2B-5682DF)
![platform](https://img.shields.io/static/v1?label=platform&message=mac-intel%20|%20mac-arm&color=blue)
[![license](https://img.shields.io/github/license/miyako/4d-plugin-continuity-camera)](LICENSE)
![downloads](https://img.shields.io/github/downloads/miyako/4d-plugin-continuity-camera/total)

**Important**: `2.0.0` breaks compatibility.

new `status` object:

* status.success: boolean
* status.document: picture (PDF document, if available)
* status.images[]: collection of TIFF pictures, typically in 3 resolutions


### Example

```4d
$options:=New object
$options.window:=Current form window
GET MOUSE($x;$y;$buttonDown)
$options.x:=$x
$options.y:=$y

$status:=Continuity camera menu ($options)

var $image : Picture

Form.image:=$image*0

If ($status.success)
	
	For each ($image; $status.images)
		Form.image:=Form.image/$image
	End for each 
	
	If ($status.document#Null)
		$folder:=Folder(Temporary folder; fk platform path).folder(Generate UUID)
		$folder.create()
		$file:=$folder.file("scan.pdf")
		PICTURE TO BLOB($status.document; $data; ".pdf")
		$file.setContent($data)
		SHOW ON DISK($file.platformPath)
	End if 
	
End if 
```

A context menu for image import is displayed.

<img width="600" alt="menu" src="https://user-images.githubusercontent.com/1725068/131959990-34a71741-ad9b-40e3-9f4c-32f84c803317.png">

If there is an iOS device nearby with the same Apple ID, it will switch to [Continuity Camera](https://developer.apple.com/documentation/appkit/supporting_continuity_camera_in_your_mac_app?language=objc) mode.

<img width="600" alt="ipad" src="https://user-images.githubusercontent.com/1725068/131960007-06262440-56a4-453b-bbdb-dd54a06ef62d.jpeg">

When you tap "Done", the command returns the image edited on iOS.
