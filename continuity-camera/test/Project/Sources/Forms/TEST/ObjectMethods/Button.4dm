$options:=New object:C1471
$options.window:=Current form window:C827
GET MOUSE:C468($x;$y;$buttonDown)
$options.x:=$x
$options.y:=$y

$status:=Continuity camera menu ($options)

If ($status.success)
	Form:C1466.image:=$status.image
End if 