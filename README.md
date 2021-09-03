# 4d-plugin-continuity-camera
Invoke macOS 10.14 Continuity Camera feature for image import

### Example

```4d
$options:=New object
$options.window:=Current form window
GET MOUSE($x;$y;$buttonDown)
$options.x:=$x
$options.y:=$y

$status:=Continuity camera menu ($options)

If ($status.success)
	Form.image:=$status.image
End if 
```

A context menu for image import is displayed.

<img width="600" alt="menu" src="https://user-images.githubusercontent.com/1725068/131959990-34a71741-ad9b-40e3-9f4c-32f84c803317.png">

If there is an iOS device neavy with the same Apple ID, it will switch to [Continuity Camera](https://developer.apple.com/documentation/appkit/supporting_continuity_camera_in_your_mac_app?language=objc) mode.

<img width="600" alt="ipad" src="https://user-images.githubusercontent.com/1725068/131960007-06262440-56a4-453b-bbdb-dd54a06ef62d.jpeg">

