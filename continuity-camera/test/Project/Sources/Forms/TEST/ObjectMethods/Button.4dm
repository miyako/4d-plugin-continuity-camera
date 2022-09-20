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
	
	If ($status.document#Null:C1517)
		$folder:=Folder:C1567(Temporary folder:C486; fk platform path:K87:2).folder(Generate UUID:C1066)
		$folder.create()
		$file:=$folder.file("scan.pdf")
		PICTURE TO BLOB:C692($status.document; $data; ".pdf")
		$file.setContent($data)
		SHOW ON DISK:C922($file.platformPath)
	End if 
	
End if 