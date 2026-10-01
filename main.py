import cv2

from parking import ParkingManagement

# Video capture
cap = cv2.VideoCapture("IMG_3761.mp4")




# Initialize parking management object
parking_manager =  ParkingManagement(
    model="yolo11s.pt",# path to model file
    classes=[2],
    json_file="bounding_boxes.json",  # path to parking annotations file
)

while cap.isOpened():
    ret, im0 = cap.read()
    im01=cv2.resize(im0,(1080,600))
    if not ret:
        break
    im0 = parking_manager.process_data(im01)
    cv2.imshow("im0",im0)
    if cv2.waitKey(1)&0xFF==27:
        break
cap.release()
cv2.destroyAllWindows()