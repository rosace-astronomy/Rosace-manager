curl http://localhost:8000/observations \
  -H "Content-Type: application/json" \
  -d '{
    "target": {
      "name": "HD 209458",
      "ra": 330.795,
      "dec": 18.884,
      "magnitude": 7.65,
      "target_type": "science"
    },
    "exposure": {
      "exposure_time": 1800,
      "num_exposures": 3,
      "grating": "R1200",
      "central_wavelength": 6563.0
    },
    "priority": 8
  }'
