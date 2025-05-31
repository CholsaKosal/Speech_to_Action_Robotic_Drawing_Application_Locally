# backend/image_processing_engine.py
import cv2
import numpy as np
import math
import os # For path joining if saving temp edge images
import config # Import our configuration

# --- Helper Function (from original main.py) ---
def calculate_distance(p1, p2):
    """Calculates Euclidean distance between two points (x, y)."""
    if p1 is None or p2 is None: return float('inf')
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

# --- Core Image Processing Functions (adapted from original main.py) ---
def get_image_contours(image_path, threshold1, threshold2, save_edge_path_prefix=None):
    """
    Convert image to contours using specific thresholds.
    :param image_path: Path to the input image.
    :param threshold1: Lower threshold for Canny edge detection.
    :param threshold2: Upper threshold for Canny edge detection.
    :param save_edge_path_prefix: Optional prefix to save the edge image for preview (e.g., "temp_edges").
    :return: List of contours (pixel coordinates), image_width, image_height, or (None, 0, 0) on failure.
    """
    if not os.path.exists(image_path):
        print(f"Error: Image path does not exist: {image_path}")
        return None, 0, 0

    image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        print(f"Error: Could not read image at {image_path}")
        return None, 0, 0

    image_height, image_width = image.shape[:2]
    if image_height == 0 or image_width == 0:
         print("Error: Invalid image dimensions.")
         return None, 0, 0

    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    edges = cv2.Canny(blurred, threshold1, threshold2)

    if save_edge_path_prefix:
        try:
            # Ensure the directory for saved edges exists if it's part of the prefix
            edge_save_dir = os.path.dirname(save_edge_path_prefix)
            if edge_save_dir and not os.path.exists(edge_save_dir):
                os.makedirs(edge_save_dir, exist_ok=True)
            
            # Construct a unique filename for the edge image
            base, ext = os.path.splitext(os.path.basename(image_path))
            edge_filename = f"{save_edge_path_prefix}_{base}_t{threshold1}-{threshold2}.png"
            cv2.imwrite(edge_filename, edges)
            print(f"Edge image saved to {edge_filename}")
        except Exception as e:
            print(f"Failed to save edge image: {e}")

    contours_cv, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    
    # Filter contours by length and convert to list of (x,y) tuples
    contours_xy = []
    for contour in contours_cv:
        if cv2.arcLength(contour, closed=False) > config.MIN_CONTOUR_LENGTH_PX:
            points = contour.squeeze().tolist()
            if not isinstance(points, list) or not points: continue
            if isinstance(points[0], int): # Handle single point contour
                points = [points]
            
            # Ensure points are valid pairs and add to list
            current_contour_points = []
            for p_arr in points:
                if isinstance(p_arr, (list, tuple)) and len(p_arr) == 2:
                    current_contour_points.append(tuple(p_arr))
                elif isinstance(p_arr, np.ndarray) and p_arr.shape == (2,): # Handle numpy array points
                    current_contour_points.append(tuple(p_arr.tolist()))

            if current_contour_points: # Only add if we have valid points
                contours_xy.append(current_contour_points)
                
    return contours_xy, image_width, image_height

def scale_contour_point(point_xy, image_width, image_height, target_width_mm, target_height_mm):
    """ Scales and transforms a single (x, y) pixel coordinate to centered target (mm)."""
    x_pixel, y_pixel = point_xy
    
    # Determine overall scale factor to fit within target dimensions while maintaining aspect ratio
    scale_x_factor = target_width_mm / image_width
    scale_y_factor = target_height_mm / image_height
    scale_factor = min(scale_x_factor, scale_y_factor)

    # Calculate offsets to center the scaled image within the target area
    scaled_img_width = image_width * scale_factor
    scaled_img_height = image_height * scale_factor
    offset_x_mm = (target_width_mm - scaled_img_width) / 2
    offset_y_mm = (target_height_mm - scaled_img_height) / 2
    
    # Transform pixel coordinates to scaled mm, centered
    # Image origin (0,0) is top-left. Robot drawing origin (0,0) for offsets is center.
    # Python X (image width) maps to RAPID X offset
    # Python Y (image height) maps to RAPID Z offset (left/right on paper)
    
    # Scale pixel to mm relative to image top-left
    x_mm_from_origin = x_pixel * scale_factor
    y_mm_from_origin = y_pixel * scale_factor

    # Center it: For robot X (maps to image X), 0 is center of paper.
    # For robot Z (maps to image Y), 0 is center of paper.
    # RAPID X = (scaled_x_pixel - scaled_image_width/2)
    # RAPID Z = -(scaled_y_pixel - scaled_image_height/2) (invert Y because image Y is down, paper Z might be up/right)
    # However, your RAPID code uses Offs(WorkSpaceCenter1, x, y, z)
    # where Python X -> RAPID x, Python Z_depth -> RAPID y, Python Y -> RAPID z.
    # Let's assume WorkSpaceCenter1 is the center of the A4 paper.
    # The output X_py, Y_py from this function will be the offsets for RAPID x and z.

    # Convert pixel x to be relative to the center of the image
    x_centered_pixel = x_pixel - (image_width / 2)
    # Convert pixel y to be relative to the center of the image, and invert (image y is down)
    y_centered_pixel = (image_height / 2) - y_pixel 

    x_py_offset = x_centered_pixel * scale_factor
    y_py_offset = y_centered_pixel * scale_factor # This will be used as the Y-coordinate in the Python (X,Z,Y) tuple

    return (x_py_offset, y_py_offset)


def generate_robot_drawing_commands(contours_xy, image_width, image_height, optimize_paths=True):
    """ 
    Takes list of contours (pixel coordinates), scales them, creates drawing paths (X_py, Z_py, Y_py).
    Z_py is the pen height (config.PEN_UP_Z_PY or config.PEN_DOWN_Z_PY).
    X_py and Y_py are the planar coordinates for drawing.
    """
    if not contours_xy or image_width <= 0 or image_height <= 0:
        return []

    scaled_contours = []
    for contour in contours_xy:
        if not contour: continue
        scaled_contour_points = [
            scale_contour_point(p, image_width, image_height, 
                                config.A4_DRAWING_AREA_WIDTH_MM, config.A4_DRAWING_AREA_HEIGHT_MM)
            for p in contour
        ]
        if len(scaled_contour_points) >= 1: # Allow single points to be drawn
            scaled_contours.append(scaled_contour_points)

    if not scaled_contours:
        return []

    # Path Optimization (simplified from original, can be enhanced)
    ordered_contours = []
    if optimize_paths and scaled_contours:
        remaining_contours = list(scaled_contours)
        current_point = (0,0) # Assume starting near center or last point of previous operation

        while remaining_contours:
            best_contour_idx = -1
            min_dist = float('inf')
            reverse_needed = False

            for i, contour_to_check in enumerate(remaining_contours):
                start_pt = contour_to_check[0]
                end_pt = contour_to_check[-1]
                
                dist_to_start = calculate_distance(current_point, start_pt)
                dist_to_end = calculate_distance(current_point, end_pt)

                if dist_to_start < min_dist:
                    min_dist = dist_to_start
                    best_contour_idx = i
                    reverse_needed = False
                
                if dist_to_end < min_dist: # Check if starting from the end is better
                    min_dist = dist_to_end
                    best_contour_idx = i
                    reverse_needed = True
            
            if best_contour_idx != -1:
                next_contour = remaining_contours.pop(best_contour_idx)
                if reverse_needed:
                    next_contour.reverse()
                ordered_contours.append(next_contour)
                current_point = next_contour[-1] # Update current point for next iteration
            else:
                break # Should not happen if remaining_contours is not empty
        processed_contours = ordered_contours
    else:
        processed_contours = scaled_contours

    robot_commands_xyz_py = [] # List of (X_py, Z_depth_py, Y_py) tuples
    for contour_points in processed_contours:
        if not contour_points: continue
        
        start_x_py, start_y_py = contour_points[0]
        # Move pen up to the start of the contour
        robot_commands_xyz_py.append((start_x_py, config.PEN_UP_Z_PY, start_y_py))
        # Move pen down at the start of the contour
        robot_commands_xyz_py.append((start_x_py, config.PEN_DOWN_Z_PY, start_y_py))

        # Draw the rest of the contour
        for i in range(len(contour_points)): # Iterate through all points including start
            pt_x_py, pt_y_py = contour_points[i]
            # Add point with pen down (if it's not the very first point already added)
            if i > 0 or len(contour_points) == 1: # For single point contours, ensure it's drawn
                 robot_commands_xyz_py.append((pt_x_py, config.PEN_DOWN_Z_PY, pt_y_py))

        # Lift pen at the end of the contour
        end_x_py, end_y_py = contour_points[-1]
        robot_commands_xyz_py.append((end_x_py, config.PEN_UP_Z_PY, end_y_py))
        
    return robot_commands_xyz_py


def process_image_to_robot_commands_pipeline(image_filepath, 
                                             canny_thresh1=config.DEFAULT_CANNY_THRESHOLD1, 
                                             canny_thresh2=config.DEFAULT_CANNY_THRESHOLD2,
                                             optimize=True):
    """
    Main pipeline function to take an image path and return a list of robot drawing commands.
    Each command is a tuple (X_py, Z_depth_py, Y_py).
    """
    print(f"Processing image: {image_filepath} with Canny thresholds: {canny_thresh1}, {canny_thresh2}")
    
    # Define a path prefix if you want to save intermediate edge images for debugging
    # For example, in the qr_uploads directory or a dedicated 'debug_edges' directory.
    # Ensure this directory exists if you use it.
    # debug_edge_save_prefix = os.path.join(os.path.dirname(image_filepath), "edge_previews", os.path.basename(image_filepath))
    debug_edge_save_prefix = None # Disable saving edge images by default

    contours, img_w, img_h = get_image_contours(image_filepath, canny_thresh1, canny_thresh2, save_edge_path_prefix=debug_edge_save_prefix)

    if contours is None or not contours:
        print("No contours found or error in contour extraction.")
        return []

    print(f"Found {len(contours)} contours. Image dimensions: {img_w}x{img_h}")
    
    robot_drawing_cmds = generate_robot_drawing_commands(contours, img_w, img_h, optimize_paths=optimize)
    
    print(f"Generated {len(robot_drawing_cmds)} robot drawing commands.")
    return robot_drawing_cmds

if __name__ == '__main__':
    # Test the pipeline
    # Create a dummy image for testing if you don't have one readily available
    # For this test, ensure you have an image in your project, e.g., 'backend/qr_uploads/test_image.png'
    # Or use an absolute path to an image.
    
    # Make sure qr_uploads directory exists for the test
    if not os.path.exists(config.QR_UPLOAD_FOLDER):
        os.makedirs(config.QR_UPLOAD_FOLDER)

    # Create a simple test image
    test_image_name = "test_square.png"
    test_image_path = os.path.join(config.QR_UPLOAD_FOLDER, test_image_name)
    if not os.path.exists(test_image_path):
        img = np.zeros((200, 200, 1), dtype="uint8")
        cv2.rectangle(img, (50, 50), (150, 150), (255), thickness=3) # White square on black
        cv2.imwrite(test_image_path, img)
        print(f"Created dummy test image: {test_image_path}")

    if os.path.exists(test_image_path):
        print(f"\n--- Testing image processing pipeline with {test_image_path} ---")
        commands = process_image_to_robot_commands_pipeline(test_image_path)
        if commands:
            print(f"\nFirst 5 generated commands (X_py, Z_depth_py, Y_py):")
            for cmd in commands[:5]:
                print(cmd)
            print("...")
            print(f"Last 5 generated commands (X_py, Z_depth_py, Y_py):")
            for cmd in commands[-5:]:
                print(cmd)
        else:
            print("No commands generated.")
    else:
        print(f"Test image not found: {test_image_path}. Skipping pipeline test.")

