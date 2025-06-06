# backend/image_processing_engine.py
import cv2
import numpy as np
import math
import os 
import config 
import logging 

def calculate_distance(p1, p2):
    """Calculates Euclidean distance between two points (x, y)."""
    if p1 is None or p2 is None: return float('inf')
    return math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

def get_canny_edges_array(image_path_or_array, threshold1, threshold2):
    """
    Generates a Canny edge detected image array.
    """
    if isinstance(image_path_or_array, str):
        # *** ADDED LOGGING ***
        logging.info(f"Reading image for Canny from path: {image_path_or_array}")
        if not os.path.exists(image_path_or_array):
            logging.error(f"Image path does not exist: {image_path_or_array}")
            return None
        image = cv2.imread(image_path_or_array, cv2.IMREAD_GRAYSCALE)
        if image is None:
            logging.error(f"cv2.imread failed to read image at {image_path_or_array}. Check file integrity and permissions.")
            return None
    elif isinstance(image_path_or_array, np.ndarray):
        logging.info("Processing Canny on a pre-loaded numpy array.")
        if len(image_path_or_array.shape) == 3: # BGR
            image = cv2.cvtColor(image_path_or_array, cv2.COLOR_BGR2GRAY)
        elif len(image_path_or_array.shape) == 2: # Already Grayscale
            image = image_path_or_array
        else:
            logging.error("Invalid NumPy array format for image.")
            return None
    else:
        logging.error(f"Invalid input type for get_canny_edges_array: {type(image_path_or_array)}")
        return None

    image_height, image_width = image.shape[:2]
    if image_height == 0 or image_width == 0:
         logging.error("Invalid image dimensions for Canny edge detection.")
         return None

    blurred = cv2.GaussianBlur(image, (5, 5), 0)
    edges = cv2.Canny(blurred, threshold1, threshold2)
    
    return edges


def get_image_contours(image_path, threshold1, threshold2, save_edge_path_prefix=None):
    """
    Convert image to contours using specific thresholds.
    """
    edges = get_canny_edges_array(image_path, threshold1, threshold2)
    if edges is None:
        return None, 0, 0
        
    image_height, image_width = edges.shape[:2]

    # ... (rest of the function is unchanged) ...
    contours_cv, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    
    contours_xy = []
    for contour in contours_cv:
        if cv2.arcLength(contour, closed=False) > config.MIN_CONTOUR_LENGTH_PX:
            points = contour.squeeze().tolist()
            if not isinstance(points, list) or not points: continue
            if isinstance(points[0], int): 
                points = [points]
            
            current_contour_points = []
            for p_arr in points:
                if isinstance(p_arr, (list, tuple)) and len(p_arr) == 2:
                    current_contour_points.append(tuple(p_arr))
                elif isinstance(p_arr, np.ndarray) and p_arr.shape == (2,): 
                    current_contour_points.append(tuple(p_arr.tolist()))

            if current_contour_points: 
                contours_xy.append(current_contour_points)
                
    return contours_xy, image_width, image_height

def scale_contour_point(point_xy, image_width, image_height, target_width_mm, target_height_mm):
    """ Scales and transforms a single (x, y) pixel coordinate to centered target (mm)."""
    x_pixel, y_pixel = point_xy
    
    scale_x_factor = target_width_mm / image_width
    scale_y_factor = target_height_mm / image_height
    scale_factor = min(scale_x_factor, scale_y_factor)

    x_centered_pixel = x_pixel - (image_width / 2)
    y_centered_pixel = (image_height / 2) - y_pixel 

    x_py_offset = x_centered_pixel * scale_factor
    y_py_offset = y_centered_pixel * scale_factor 
    return (x_py_offset, y_py_offset)


def generate_robot_drawing_commands(contours_xy, image_width, image_height, optimize_paths=True):
    """ 
    Takes list of contours (pixel coordinates), scales them, creates drawing paths (X_py, Z_depth_py, Y_py).
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
        if len(scaled_contour_points) >= 1: 
            scaled_contours.append(scaled_contour_points)

    if not scaled_contours:
        return []

    ordered_contours = []
    if optimize_paths and scaled_contours:
        remaining_contours = list(scaled_contours)
        current_point = (0,0) 

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
                
                if dist_to_end < min_dist: 
                    min_dist = dist_to_end
                    best_contour_idx = i
                    reverse_needed = True
            
            if best_contour_idx != -1:
                next_contour = remaining_contours.pop(best_contour_idx)
                if reverse_needed:
                    next_contour.reverse()
                ordered_contours.append(next_contour)
                current_point = next_contour[-1] 
            else:
                break 
        processed_contours = ordered_contours
    else:
        processed_contours = scaled_contours

    robot_commands_xyz_py = [] 
    for contour_points in processed_contours:
        if not contour_points: continue
        
        start_x_py, start_y_py = contour_points[0]
        robot_commands_xyz_py.append((start_x_py, config.PEN_UP_Z_PY, start_y_py))
        robot_commands_xyz_py.append((start_x_py, config.PEN_DOWN_Z_PY, start_y_py))

        for i in range(len(contour_points)): 
            pt_x_py, pt_y_py = contour_points[i]
            if i > 0 or len(contour_points) == 1: 
                 robot_commands_xyz_py.append((pt_x_py, config.PEN_DOWN_Z_PY, pt_y_py))

        end_x_py, end_y_py = contour_points[-1]
        robot_commands_xyz_py.append((end_x_py, config.PEN_UP_Z_PY, end_y_py))
        
    return robot_commands_xyz_py


def process_image_to_robot_commands_pipeline(image_filepath, 
                                             canny_thresh1=config.DEFAULT_CANNY_THRESHOLD1, 
                                             canny_thresh2=config.DEFAULT_CANNY_THRESHOLD2,
                                             optimize=True):
    """
    Main pipeline function to take an image path and return a list of robot drawing commands.
    """
    logging.info(f"Pipeline started for image: {image_filepath} with Canny: {canny_thresh1}, {canny_thresh2}")
    
    contours, img_w, img_h = get_image_contours(image_filepath, canny_thresh1, canny_thresh2)

    if contours is None or not contours:
        logging.warning("No contours found or error in contour extraction.")
        return []

    logging.info(f"Found {len(contours)} contours. Generating robot commands...")
    
    robot_drawing_cmds = generate_robot_drawing_commands(contours, img_w, img_h, optimize_paths=optimize)
    
    logging.info(f"Generated {len(robot_drawing_cmds)} robot drawing commands.")
    return robot_drawing_cmds
