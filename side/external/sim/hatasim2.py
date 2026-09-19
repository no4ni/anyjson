import pybullet as p
import pybullet_data
import time
import json
import os
from pathlib import Path
import numpy as np
import threading
import queue
import win32gui
import win32con
import win32api
import cv2
import math

class JerichoSimulator:
	def __init__(self):
		# Основной клиент с GUI (физика + визуал)
		self.client = p.connect(p.GUI)
		p.configureDebugVisualizer(p.COV_ENABLE_GUI, 0)
		p.configureDebugVisualizer(p.COV_ENABLE_WIREFRAME, 0)
		p.setAdditionalSearchPath(pybullet_data.getDataPath())
		p.setGravity(0, 0, -9.81)
		p.setTimeStep(1./240.)
		self.cam_distance = 2
		self.cam_yaw = 50
		self.cam_pitch = -30
		# 1. Стандартные модели (робот и камера)
		self.robot = p.loadURDF("r2d2.urdf", [0, 0, 0.5], globalScaling=0.3)
		
		# 2. Загрузка квартиры (визуал, коллизия, пол)
		_scene_env = os.environ.get("SIM_SCENE_DIR")
		if _scene_env:
			self.scene_dir = Path(_scene_env)
		else:
			self.scene_dir = Path(__file__).resolve().parent
		visual_path = self.scene_dir / "hata.obj"
		collision_path = self.scene_dir / "hatacol.obj"
		
		rot_quat = p.getQuaternionFromEuler([-math.radians(90), -math.radians(-180), 0])
		
		if visual_path.exists():
			print(f"🏠 Загружаю визуал: {visual_path}")
			p.setAdditionalSearchPath(str(self.scene_dir))
			
			visual_id = p.createVisualShape(p.GEOM_MESH, fileName="hata.obj", meshScale=[1,1,1])
			self.apartment_visual = p.createMultiBody(0, -1, visual_id)
			p.resetBasePositionAndOrientation(self.apartment_visual, [2, -20, 0], rot_quat)
			
			if collision_path.exists():
				print(f"📦 Загружаю физику: {collision_path}")
				collision_id = p.createCollisionShape(
					p.GEOM_MESH,
					fileName="hatacol.obj",
					meshScale=[1, 1, 1],
					flags=p.GEOM_FORCE_CONCAVE_TRIMESH
				)
				self.apartment_collision = p.createMultiBody(0, collision_id, -1)
				p.resetBasePositionAndOrientation(self.apartment_collision, [2, -20, 0], rot_quat)
				
				floor_z = -1
				floor_collision = p.createCollisionShape(p.GEOM_BOX, halfExtents=[100, 100, 1])
				self.floor = p.createMultiBody(
					baseMass=0,
					baseCollisionShapeIndex=floor_collision,
					baseVisualShapeIndex=-1,
					basePosition=[0, 0, floor_z],
					baseOrientation=[0, 0, 0, 1]
				)
				p.resetBasePositionAndOrientation(self.robot, [0, 0, floor_z + 0.5], [0,0,0,1])
				print(f"✅ Пол на высоте {floor_z:.2f} м, робот поднят")
		else:
			print(f"⚠️ Файл {visual_path} не найден")
			floor_z = -1.0
		
		# 3. Отдельный клиент DIRECT для чистой камеры
		self.client_cam = p.connect(p.DIRECT)
		p.setAdditionalSearchPath(pybullet_data.getDataPath(), physicsClientId=self.client_cam)
		p.setAdditionalSearchPath(str(self.scene_dir), physicsClientId=self.client_cam)
		
		visual_id_cam = p.createVisualShape(p.GEOM_MESH, fileName="hata.obj", meshScale=[1,1,1], physicsClientId=self.client_cam)
		self.apartment_visual_cam = p.createMultiBody(0, -1, visual_id_cam, physicsClientId=self.client_cam)
		p.resetBasePositionAndOrientation(self.apartment_visual_cam, [2, -20, 0], rot_quat, physicsClientId=self.client_cam)
		
		r2d2_path = os.path.join(pybullet_data.getDataPath(), "r2d2.urdf")
		self.robot_cam = p.loadURDF(r2d2_path, [0, 0, floor_z + 0.5], globalScaling=0.3, useFixedBase=False, physicsClientId=self.client_cam)
		p.setGravity(0,0,0, physicsClientId=self.client_cam)
		
		self.robot_view_positioned = False
		
		# Интерфейс и консоль
		_cmd_env = os.environ.get("SIM_CMD_FILE")
		if _cmd_env:
			self.command_file = Path(_cmd_env)
		else:
			self.command_file = self.scene_dir / "command.json"
		self.running = True
		self.command_queue = queue.Queue()
		self.step_counter = 0
		self.console_thread = threading.Thread(target=self.console_reader, daemon=True)
		self.console_thread.start()
		print("✅ Симулятор запущен. Вводите команды в консоли...")
		
		time.sleep(0.5)
		hwnd = win32gui.FindWindow(None, "Bullet Physics ExampleBrowser using OpenGL3+ [btgl] Release build")
		if hwnd:
			screen_width = win32api.GetSystemMetrics(0)
			win_width = 300
			win_height = 600
			x = screen_width - win_width
			y = 0
			win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, x, y, win_width, win_height, win32con.SWP_SHOWWINDOW)

	def console_reader(self):
		while self.running:
			try:
				cmd = input()
				if cmd.strip():
					self.command_queue.put(cmd.strip())
			except EOFError:
				break

	def get_lidar(self, num_rays=7, max_dist=10.0):
		pos, orn = p.getBasePositionAndOrientation(self.robot)
		start_pos = [pos[0], pos[1], pos[2] + 0.3]
		euler = p.getEulerFromQuaternion(orn)
		yaw = euler[2]
		angles = np.linspace(-np.pi/3, np.pi/3, num_rays) + yaw
		distances = np.full(num_rays, max_dist)
		for i, angle in enumerate(angles):
			dx = np.cos(angle)
			dy = np.sin(angle)
			to = [start_pos[0] + dx * max_dist, start_pos[1] + dy * max_dist, start_pos[2]]
			ray = p.rayTest(start_pos, to)
			if ray[0][0] != -1:
				distances[i] = ray[0][2] * max_dist
		return angles, distances

	def format_lidar(self, angles, distances):
		yaw = p.getEulerFromQuaternion(p.getBasePositionAndOrientation(self.robot)[1])[2]
		rel_angles = [(a - yaw + np.pi) % (2*np.pi) - np.pi for a in angles]
		sorted_pairs = sorted(zip(rel_angles, distances), key=lambda x: x[0])
		parts = []
		for angle_rad, dist in sorted_pairs:
			angle_deg = round(np.degrees(angle_rad))
			parts.append(f"{angle_deg}°:{dist:.2f}м")
		return " | ".join(parts)

	def show_robot_view(self):
		pos, orn = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)
		p.resetBasePositionAndOrientation(self.robot_cam, pos, orn, physicsClientId=self.client_cam)
		
		cam_pos = [pos[0], pos[1], pos[2] + 0.5]
		rot = p.getMatrixFromQuaternion(orn)
		forward = [rot[0], rot[3], rot[6]]
		target = [cam_pos[i] + forward[i] * 2.0 for i in range(3)]
		view = p.computeViewMatrix(cam_pos, target, [0, 0, 1])
		proj = p.computeProjectionMatrixFOV(60, 4/3, 0.1, 100)
		w, h = 320, 240
		img = p.getCameraImage(w, h, view, proj, physicsClientId=self.client_cam)
		rgb = np.array(img[2], dtype=np.uint8).reshape(h, w, 4)[:, :, :3]
		
		cv2.imshow("Robot View (Camera RGB)", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
		hwnd = win32gui.FindWindow(None, "Robot View (Camera RGB)")
		if hwnd:
			win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
			if not self.robot_view_positioned:
				screen_height = win32api.GetSystemMetrics(1)
				win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 320, 240, win32con.SWP_SHOWWINDOW)
				self.robot_view_positioned = True
		cv2.waitKey(1)
		
	def apply_action(self, action):
		action_type = action.get("action", "")
		pos, orn = p.getBasePositionAndOrientation(self.robot)
		euler = p.getEulerFromQuaternion(orn)
		
		if action_type == "forward":
			speed = action.get("speed", 0.1)
			steps = max(1, int(abs(speed) / 0.01))
			step_size = speed / steps
			for _ in range(steps):
				pos, orn = p.getBasePositionAndOrientation(self.robot)
				euler = p.getEulerFromQuaternion(orn)
				yaw = euler[2]
				dx = np.cos(yaw) * step_size
				dy = np.sin(yaw) * step_size
				new_pos = [pos[0] + dx, pos[1] + dy, pos[2]]
				p.resetBasePositionAndOrientation(self.robot, new_pos, orn)
				p.stepSimulation()
				contacts = p.getContactPoints(bodyA=self.robot, bodyB=self.floor)
				if not contacts:
					p.resetBasePositionAndOrientation(self.robot, pos, orn)
					break
		elif action_type == "turn":
			angle = action.get("angle", 0.1)
			new_orn = p.getQuaternionFromEuler([0, 0, euler[2] - angle])
			p.resetBasePositionAndOrientation(self.robot, pos, new_orn)
		elif action_type == "reset":
			p.resetBasePositionAndOrientation(self.robot, [0, 0, 0.5], [0, 0, 0, 1])
		
		# После движения обновляем камеру, чтобы она следовала за роботом
		pos, orn = p.getBasePositionAndOrientation(self.robot, physicsClientId=self.client)

		# Сохраняем параметры камеры для последующего обновления
		cam = p.getDebugVisualizerCamera()
		self.cam_distance = cam[10]   # дистанция
		self.cam_yaw = cam[8]		 # рыскание
		self.cam_pitch = cam[9]	   # наклон
		# Обновляем камеру, чтобы она следовала за роботом, сохраняя ракурс
		p.resetDebugVisualizerCamera(
			cameraDistance=self.cam_distance,
			cameraYaw=self.cam_yaw,
			cameraPitch=self.cam_pitch,
			cameraTargetPosition=pos
		)
		# После получения pos, orn:
		euler = p.getEulerFromQuaternion(orn)
		yaw_deg = math.degrees(euler[2])
		compass = (yaw_deg + 360) % 360
		print(f"🧭 Компас: {compass:.1f}°")
		angles, distances = self.get_lidar()
		print(f"📡 Лидар: {self.format_lidar(angles, distances)}")
		print(f"📍 GPS: {57.164544+pos[0]*0.000009:.6f}, {65.503498+pos[1]*0.000009:.6f}") 
		p.resetBasePositionAndOrientation(self.robot_cam, pos, orn, physicsClientId=self.client_cam)
		self.show_robot_view()
		return action_type

	def parse_console_command(self, cmd_str):
		if cmd_str.startswith('{'):
			try:
				return json.loads(cmd_str)
			except:
				return None
		parts = cmd_str.split()
		if not parts:
			return None
		cmd = parts[0].lower()
		if cmd in ["quit", "exit"]:
			self.running = False
			return None
		if cmd == "reset":
			return {"action": "reset"}
		if cmd == "lidar":
			return {"action": "lidar"}
		if cmd in ["fwd", "forward"]:
			speed = float(parts[1]) if len(parts) > 1 else 0.1
			return {"action": "forward", "speed": speed}
		if cmd == "turn":
			angle = float(parts[1]) if len(parts) > 1 else 0.1
			return {"action": "turn", "angle": angle}
		return None

	def run(self):
		last_file_time = 0
		while p.isConnected() and self.running:
			if self.command_file.exists():
				mtime = os.path.getmtime(self.command_file)
				if mtime > last_file_time:
					try:
						with open(self.command_file, 'r') as f:
							cmd = json.load(f)
						self.apply_action(cmd)
						last_file_time = mtime
					except Exception as e:
						print(f"⚠️ Ошибка: {e}")
			
			try:
				while True:
					cmd_str = self.command_queue.get_nowait()
					cmd = self.parse_console_command(cmd_str)
					if cmd:
						self.apply_action(cmd)
			except queue.Empty:
				pass
			
			p.stepSimulation()
			time.sleep(1./240.)

if __name__ == "__main__":
	sim = JerichoSimulator()
	sim.run()