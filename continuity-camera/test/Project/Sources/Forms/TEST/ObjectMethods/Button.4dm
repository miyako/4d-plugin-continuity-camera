$options:=New object:C1471
$options.window:=Current form window:C827
GET MOUSE:C468($x; $y; $buttonDown)
$options.x:=$x
$options.y:=$y

$status:=Continuity camera menu($options)

var $image : Picture

Form:C1466.image:=$image*0

If ($status.success)
	
	For each ($image; $status.images)
		Form:C1466.image:=Form:C1466.image/$image
	End for each 
	
End if 